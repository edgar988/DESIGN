import base64
import json
import os
import sys
import zlib

import ezdxf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from tools import dxf_extract, takeoff as tk            # noqa: E402
from estimating import estimate as em, schedule as sm   # noqa: E402
import make_sample_dxf                                  # noqa: E402


def load(*p):
    return json.load(open(os.path.join(ROOT, *p), encoding="utf-8"))


CAT, PROG = load("config", "families.json"), load("config", "program.json")


def ga263_issued():
    """GA-263 priced from the package issued 09-2026 (the heat-load calibration), not its new drawing."""
    st = load("config", "stores", "GA-263.json")
    return dict(st, package=st["issued_package"])


def test_ga263_heat_load_reproduces_issued_report():
    """GA 263 M1 (09/22/26): 39.4 kW, added 55,551, design 61,106 Btu/h, 5.09 t, 2 x 2.5 t, ~2,470 cfm."""
    t = tk.build(ga263_issued(), CAT, PROG)
    h = t["heat_load"]
    assert h["connected_kw"] == 39.4
    assert abs(h["design_total"] - 61106) / 61106 < 0.002
    assert abs(h["added_total"] - 55551) / 55551 < 0.002
    assert h["added_latent"] == 7058
    assert h["tons_required"] == 5.09 and h["minisplit_units"] == 2
    assert abs(h["supply_cfm"] - 2470) <= 5 and h["shr"] == 0.87


def test_dxf_extract_sample(tmp_path):
    p = make_sample_dxf.build(str(tmp_path / "s.dxf"))
    lay = dxf_extract.extract(p)
    s = lay["summary"]
    assert s["walls"]["exist"] == 4 and s["walls"]["demo"] == 1 and s["walls"]["new"] == 1
    assert all(w["paired"] for w in lay["walls"])
    assert abs(lay["walls"][0]["thickness"] - 4.875) < 0.01
    assert s["room_sf"] == 300.0
    keys = sorted(q["key"] for q in lay["equipment"] if q["key"])
    assert keys == sorted(["OVENTION_C2000", "ACP_MXP22TLT", "HOBART_LXNR", "TABCO_FC3", "ADVANCE_7PS65",
                           "TRAULSEN_G22010", "HOSHIZAKI_UR48B", "ADVANCE_FSS_30x96"])
    assert lay["unmatched"] == {"MYSTERY_BLOCK_X": 1, "OLD_POPPER": 1}
    assert s["equipment_demo"] == 1
    assert len([d for d in lay["doors"] if d["status"] == "new"]) == 1
    assert "panel" in lay["points"] and "water" in lay["points"]
    assert {q["item"] for q in lay["equipment"] if q["key"] == "OVENTION_C2000"} == {"7"}


def test_store_layer_map_reads_nonstandard_wall_layer(tmp_path):
    """AutoQuotes layouts (GA 263) draw walls on "Layer1"; the store's layer_map says what that layer is."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 1
    make_sample_dxf.wall(doc.modelspace(), "Layer1", 0, 0, 120, 0, t=4.0)
    p = str(tmp_path / "aq.dxf")
    doc.saveas(p)
    assert dxf_extract.extract(p)["walls"] == []
    walls = dxf_extract.extract(p, {"layer_map": {"Layer1": "A-WALL"}})["walls"]
    assert len(walls) == 1 and walls[0]["status"] == "exist" and abs(walls[0]["thickness"] - 4.0) < 0.01


def test_plan_window_reads_only_that_part_of_the_drawing(tmp_path):
    """TX / NJ drawings hold a second copy of the plan; the store's plan_window picks the one to read."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 1
    make_sample_dxf.wall(doc.modelspace(), "0", 0, 0, 120, 0, t=4.0)
    make_sample_dxf.wall(doc.modelspace(), "0", 0, 600, 120, 600, t=4.0)       # the copy
    p = str(tmp_path / "two.dxf")
    doc.saveas(p)
    store = {"layer_map": {"0": "A-WALL"}}
    assert len(dxf_extract.extract(p, store)["walls"]) == 2
    walls = dxf_extract.extract(p, dict(store, plan_window=[-50, -50, 200, 200]))["walls"]
    assert len(walls) == 1 and abs(walls[0]["start"][1]) < 1


def test_master_numbers_by_model_size_and_swap(tmp_path):
    """Master numbering (Edgar): by model (G10011 -> PH2), by the master's equivalents (UR48B is the 60 in.
    Atosa, item 6), by footprint for table stand-ins (TFSS 48x30 -> fab table 2.2); a 7-PS-65 fits new 1 and
    existing X2, takes 1 and is reported; a store item_override wins."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    for name, (w, d) in {"G10011": (30, 35), "UR48B": (48, 30), "TFSS-304": (48, 30), "7-PS-65": (17, 17)}.items():
        make_sample_dxf.rect_block(doc, name, w, d, attribs=())
    refs = {n: msp.add_blockref(n, (100 * i, 0)) for i, n in enumerate(("G10011", "UR48B", "TFSS-304", "7-PS-65"))}
    p = str(tmp_path / "m.dxf")
    doc.saveas(p)
    lay = dxf_extract.extract(p, {"item_numbers": "master"})
    got = dict((q["block"], (q["item"], q["key"], q["provided_by"])) for q in lay["equipment"])
    assert got["G10011"] == ("PH2", "TRAULSEN_G10011", "OWNER")
    assert got["UR48B"] == ("6", "ATOSA_AUF60SD", "AEQ")
    assert got["TFSS-304"] == ("2.2", "AEQ_FABSS_4830", "AEQ")
    assert got["7-PS-65"] == ("1", "ADVANCE_7PS65", "AEQ")
    assert any(w.startswith("Item 1 chosen") and "X2" in w for w in lay["warnings"])
    over = {"item_numbers": "master", "item_overrides": {refs["7-PS-65"].dxf.handle: "X2"}}
    q = next(q for q in dxf_extract.extract(p, over)["equipment"] if q["block"] == "7-PS-65")
    assert (q["item"], q["provided_by"]) == ("X2", "EXISTING")


def test_item_prefix_sets_who_provides_it():
    """Edgar: plain number = AEQ supplies and installs, X = existing (E in older drawings), PH = owner."""
    assert [dxf_extract.provided_by(n) for n in ("4", "2.1", "X1", "E3", "PH2", "", None)] == \
        ["AEQ", "AEQ", "EXISTING", "EXISTING", "OWNER", None, None]


def _aq_xrecord(doc, name, items):
    """AutoQuotes-style root record: XML dictionary keyed by block handle -> base64 -> raw DEFLATE."""
    body = "".join("<item><key><long>%d</long></key><value><V>%s</V></value></item>"
                   % (h, "".join("<%s>%s</%s>" % (k, v, k) for k, v in f.items())) for h, f in items.items())
    xml = '<?xml version="1.0" encoding="utf-16"?><D><__dictionary>%s</__dictionary></D>' % body
    co = zlib.compressobj(wbits=-15)
    raw = co.compress(base64.b64encode(xml.encode())) + co.flush()
    xr = doc.objects.add_xrecord(doc.rootdict.dxf.handle)
    xr.reset([(310, raw[i:i + 127]) for i in range(0, len(raw), 127)])
    doc.rootdict[name] = xr


def test_autoquotes_item_numbers_and_wall_roughin(tmp_path):
    """An AQ block with an unhelpful name matches on its AQ model and carries the AQ item number; its
    rough-in lands on the room face of the wall behind it; the package check reports what is missing."""
    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 1
    msp = doc.modelspace()
    make_sample_dxf.wall(msp, "A-WALL", 0, 144, 300, 144)
    make_sample_dxf.rect_block(doc, "AQSL_GENERIC_TABLE", 60, 30, attribs=())
    ref = msp.add_blockref("AQSL_GENERIC_TABLE", (100, 110), dxfattribs={"layer": "AQSL-PlanView"})
    h = int(ref.dxf.handle, 16)
    _aq_xrecord(doc, "AQSL-AQXBLOCKDATA", {h: {"Manufacturer": "Advance Tabco", "Model": "KLG-365",
                                              "Width": "60", "Depth": "30"}})
    _aq_xrecord(doc, "AQSL-AQXPROJECTDATA", {h: {"LineItemNumber": "10"}})
    p = str(tmp_path / "aq.dxf")
    doc.saveas(p)
    store = {"package": [{"item": 10, "key": "TABCO_KLG365", "qty": 1}, {"item": 15, "key": "MTI_AUTOFRY5", "qty": 1}]}
    lay = dxf_extract.extract(p, store)
    q = next(q for q in lay["equipment"] if q["block"] == "AQSL_GENERIC_TABLE")
    assert q["key"] == "TABCO_KLG365" and q["item"] == "10"
    assert q["roughin"] == [130.0, round(144 - 4.875 / 2, 2)]
    assert q["revit_rotation"] == 0.0           # back to the north wall, front facing south into the room
    assert lay["items"] == [{"item": "10", "label": "Advance Tabco KLG-365", "key": "TABCO_KLG365", "count": 1,
                             "provided_by": "AEQ"}]
    assert any("Package item 15 MTI_AUTOFRY5 is not on the drawing" in w for w in lay["warnings"])


def test_takeoff_and_estimate_from_dxf(tmp_path):
    p = make_sample_dxf.build(str(tmp_path / "s.dxf"))
    store = load("config", "stores", "TX-093.json")
    lay = dxf_extract.extract(p, store)
    t = tk.build(store, CAT, PROG, lay)
    q = t["quantities"]
    assert q["new_partition_lf"] == 5.0 and q["demo_partition_lf"] == 5.0
    assert q["demo_equipment"] == 1 and q["doors_new"] == 1
    # every electrified item gets a circuit; routing uses the panel point
    assert len(t["circuits"]) == 5          # C2000, MXP22, LXnR, G22010, UR48B
    assert all(c["home_run_lf"] > 14 for c in t["circuits"])
    e = em.estimate(t, store, PROG)
    assert abs(sum(e["customer_sections"].values()) - e["total"]) < 0.01
    assert e["total"] % 100 == 0 and e["margin_pct"] > 25
    s = sm.build(e, store)
    assert s["turnover"] > s["site_start"] > s["ntp"]


def test_customer_outputs_carry_no_internal_numbers(tmp_path):
    from estimating import build_quote as bq
    store = ga263_issued()
    t = tk.build(store, CAT, PROG)
    e = em.estimate(t, store, PROG)
    s = sm.build(e, store)
    pdf = bq.build_pdf(str(tmp_path / "q.pdf"), store, PROG, t, e, s, None)
    import pdfplumber
    text = "".join(pg.extract_text() for pg in pdfplumber.open(pdf).pages)
    for banned in ("true cost", "margin", "INTERNAL", "$95", "$42", "per diem %d" % 0, "Edgar Aaron"):
        assert banned.lower() not in text.lower(), banned
    assert "${:,.2f}".format(e["total"]) in text


def test_synced_family_data_fills_only_gaps():
    import copy
    cat = copy.deepcopy(CAT)
    synced = {"HOSHIZAKI_UR48B": {"revit_family": "QF_Hoshizaki_UR48B", "revit_type": "UR48B",
                                  "synced_at": "2026-10-02T09:00:00",
                                  "elec": {"volts": 115, "phase": 1, "amps": 6.4, "nema": "5-15P"},
                                  "connectors": []},
              "OVENTION_C2000": {"elec": {"volts": 240, "amps": 99}},
              "TRAULSEN_G10011": {"elec": {"amps": 99}}}
    tk.overlay_synced(cat, synced)
    g = cat["items"]["HOSHIZAKI_UR48B"]
    assert g["elec"]["amps"] == 6.4 and g["verified"] == "family"
    assert cat["items"]["OVENTION_C2000"]["elec"]["amps"] == 34.0     # verified mfr data never overwritten
    assert cat["items"]["TRAULSEN_G10011"]["elec"]["amps"] == 3.8      # verified from the master cut sheet
