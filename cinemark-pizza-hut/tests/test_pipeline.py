import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from tools import dxf_extract, takeoff as tk            # noqa: E402
from estimating import estimate as em, schedule as sm   # noqa: E402
import make_sample_dxf                                  # noqa: E402


def load(*p):
    return json.load(open(os.path.join(ROOT, *p), encoding="utf-8"))


CAT, PROG = load("config", "families.json"), load("config", "program.json")


def test_ga263_heat_load_reproduces_issued_report():
    """GA 263 M1 (09/22/26): 39.4 kW, added 55,551, design 61,106 Btu/h, 5.09 t, 2 x 2.5 t, ~2,470 cfm."""
    t = tk.build(load("config", "stores", "GA-263.json"), CAT, PROG)
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
    store = load("config", "stores", "GA-263.json")
    t = tk.build(store, CAT, PROG)
    e = em.estimate(t, store, PROG)
    s = sm.build(e, store)
    pdf = bq.build_pdf(str(tmp_path / "q.pdf"), store, PROG, t, e, s, None)
    import pdfplumber
    text = "".join(pg.extract_text() for pg in pdfplumber.open(pdf).pages)
    for banned in ("true cost", "margin", "INTERNAL", "$95", "$42", "per diem %d" % 0, "Edgar Aaron"):
        assert banned.lower() not in text.lower(), banned
    assert "${:,.2f}".format(e["total"]) in text
