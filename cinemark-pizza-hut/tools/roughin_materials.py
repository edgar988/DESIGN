#!/usr/bin/env python3
"""Rough-in material list for purchasing (Edgar 2026-10-29: "a list with qty of each for purchasing").

    python tools/roughin_materials.py TX-093 --work C:/AEQ/work/TX-093

Reads the store's takeoff (takeoff.json: the circuits that are priced) and, when the Revit build has run, the
connection list it writes (_AEQ OUTPUT/roughin_connections.json: every connection's tag, size and height, as
on QF103 and the QF405 rough-in elevations). Writes <set name>-ROUGH-IN MATERIAL LIST-<rev>.xlsx with:

  Electrical  devices, boxes, plates, breakers, wire by size and colour, EMT and fittings, subpanel / HVAC
  Plumbing    stops, connectors, piping, traps and indirect-waste parts for the items in the plumbing scope
  Connections every connection with its tag, size and rough-in height (the field list)
  Notes       how each quantity is counted, and what to verify before ordering

Counting rules are in RULES below and in the Notes sheet. No prices: this is a purchasing count.
"""
import argparse
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from takeoff import receptacle_for, load_catalog  # noqa: E402  (one rule for drawings, quote and list)

WASTE = 1.10            # wire: 10% for waste and makeup, plus TAIL_FT at each end
TAIL_FT = 2.0
EMT_SIZE = '3/4"'

RULES = [
    "Circuits, home-run lengths and breaker sizes come from the takeoff the quote is priced from (one EMT run "
    "per circuit, panel to rough-in, up over the ceiling and down). Where circuits share a run in the field, "
    "EMT and fittings drop; wire does not.",
    "120 V cord-and-plug circuits are 20 A with a 5-20R receptacle (takes the 5-15P cords), as priced.",
    "Other cord-and-plug circuits: the receptacle matches the equipment plug (6-30P -> 6-30R, 15-50P -> "
    "15-50R); the breaker is the takeoff size (125% of nameplate) but never above the receptacle rating. "
    "Confirm MOCP on each data plate.",
    "Wire: copper THHN/THWN-2 at 75 C. Up to 20 A #12, 30 A #10, 50 A #8, 65 A #6. Ground per breaker: up to "
    "20 A #12, up to 60 A #10. Where the store gives sizes for a long run (voltage drop), those sizes. "
    "Length = home run x %.2f + %d ft at each end." % (WASTE, TAIL_FT),
    "Neutral (white) only where the circuit needs one: 120 V circuits and NEMA 14- / L14- / L21- receptacles.",
    "Hot colours: 1-pole black; 2-pole black + red; 3-pole black + red + blue.",
    "EMT per run, sized for its conductors at 40%% fill (NEC Chapter 9), %s minimum: a coupling every 10 ft, "
    "2 set-screw connectors, a strap every 10 ft plus one at each end, and 2 sweeps (up and down)." % EMT_SIZE,
    "Breakers are listed by the panel they go in when the circuits come from more than one panel.",
    "Boxes: 4\" square x 2-1/8\" deep with a 1-gang ring for 5-20R; 4-11/16\" square x 2-1/8\" deep with a "
    "single-receptacle ring for 30-50 A receptacles and direct connections. Stainless steel plates.",
    "Direct connections (the dish machine): a junction box at the rough-in and a 6 ft 3/4\" liquidtight whip.",
    "Plumbing: only the items in the store's plumbing scope (new connections). Each water connection gets a "
    "quarter-turn stop, a drop-ear elbow / stub-out, an escutcheon and a braided connector; supply piping "
    "is the takeoff length per fixture, split between its connections; hot piping is insulated.",
]

VERIFY = [
    "GFCI protection: confirm which kitchen receptacle circuits need GFCI under the NEC edition the AHJ has "
    "adopted. The 5-20R receptacles are listed as GFCI devices; 208 V circuits may need 2- or 3-pole GFCI "
    "breakers, which are not in this list.",
    "Breakers: match the existing panel's make and type (the estimate assumes Square D QO bolt-on). Confirm "
    "spare spaces.",
    "Available phase at the panel: the Ovention C2000 is listed 3-phase (34 A, NEMA 15-50P). On single-phase "
    "service it is a 44-49 A NEMA 6-50P unit (CINEMARK_PH_MASTER.pdf p.5).",
    "Existing drain at the indirect waste: height and location; air gap per the plumbing code.",
]


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def wire_awg(amps):
    for limit, awg in ((20, "12"), (30, "10"), (50, "8"), (65, "6"), (85, "4")):
        if amps <= limit:
            return awg
    return "VERIFY"


def ground_awg(amps):
    return "12" if amps <= 20 else "10" if amps <= 60 else "8" if amps <= 100 else "VERIFY"


def needs_neutral(circuit, rec):
    if (circuit.get("poles") or 1) == 1 or circuit.get("neutral"):    # or the data plate says so (Champion)
        return True
    return bool(rec and re.match(r"^(14|L14|L21|18)-", rec))


def circuit_plan(c):
    """What one takeoff circuit needs: (breaker amps, poles, receptacle or None, conductors [(awg, colour, n)])."""
    poles = int(c.get("poles") or 1)
    rec, rec_a = (None, None) if c.get("conn") == "direct" else receptacle_for(c.get("nema"))
    brk = int(c.get("breaker_a") or 20)
    if rec_a:                           # as the takeoff sizes it (older takeoffs: applied here)
        brk = rec_a if poles == 1 else min(brk, rec_a)
    given = c.get("wire") or {}         # sizes given for a long run (Edgar: 3% voltage drop)
    awg = given.get("hot") or wire_awg(brk)
    colours = ["BLACK", "RED", "BLUE"][:poles]
    cond = [(awg, col, 1) for col in colours]
    if needs_neutral(c, rec):
        cond.append((awg, "WHITE", 1))
    cond.append((given.get("ground") or ground_awg(brk), "GREEN", 1))
    return brk, poles, rec, cond


# THHN / THWN-2 area, sq in (NEC Chapter 9 Table 5), and EMT at 40% fill (Table 4)
THHN_SQIN = {"14": 0.0097, "12": 0.0133, "10": 0.0211, "8": 0.0366, "6": 0.0507, "4": 0.0824, "3": 0.0973,
             "2": 0.1158, "1": 0.1562}
EMT_40 = [('1/2"', 0.122), ('3/4"', 0.213), ('1"', 0.346), ('1-1/4"', 0.598), ('1-1/2"', 0.814)]


def emt_size(cond):
    """The smallest EMT that holds a circuit's conductors at 40% fill (never under 3/4")."""
    area = sum(THHN_SQIN.get(awg, 0.2) * n for awg, _, n in cond)
    return next((s for s, a in EMT_40 if a >= area and s != '1/2"'), "VERIFY")


def _add(d, key, qty):
    d[key] = d.get(key, 0) + qty


def electrical(takeoff, store):
    items = {}                  # (group, description) -> qty
    notes = {}                  # (group, description) -> note
    wire = {}                   # (awg, colour) -> LF
    emt_lf = {}                 # EMT size -> LF
    runs = 0
    for c in takeoff["circuits"]:
        brk, poles, rec, cond = circuit_plan(c)
        run = float(c.get("home_run_lf") or 0)
        runs += 1
        size = emt_size(cond)
        emt_lf[size] = emt_lf.get(size, 0.0) + run
        for awg, col, n in cond:
            _add(wire, (awg, col), n * (run * WASTE + 2 * TAIL_FT))
        _add(items, ("BREAKERS", "Circuit breaker, %d-pole %d A%s" % (
            poles, brk, " (%s)" % c["source_label"] if c.get("source_label") else "")), 1)
        if rec:
            if rec == "5-20R":
                _add(items, ("DEVICES", "Receptacle, NEMA 5-20R, 20 A 125 V, GFCI, commercial grade"), 1)
                _add(items, ("BOXES & PLATES", 'Box, 4" square x 2-1/8" deep, with 1-gang device ring'), 1)
                _add(items, ("BOXES & PLATES", "Plate, stainless steel, 1-gang decorator (GFCI)"), 1)
            else:
                amps = int(rec.split("-")[1][:-1])
                _add(items, ("DEVICES", "Receptacle, NEMA %s, %d A, single, commercial grade" % (rec, amps)), 1)
                _add(items, ("BOXES & PLATES", 'Box, 4-11/16" square x 2-1/8" deep, with single-receptacle ring'), 1)
                _add(items, ("BOXES & PLATES", "Plate, stainless steel, single receptacle, NEMA %s" % rec), 1)
        elif c.get("conn") == "direct":
            _add(items, ("DEVICES", "Direct connection: junction box at rough-in (%s)" % c.get("key")), 1)
            _add(items, ("BOXES & PLATES", 'Box, 4-11/16" square x 2-1/8" deep, with blank stainless cover'), 1)
            _add(items, ("DEVICES", 'Whip, 3/4" liquidtight flexible conduit, 6 ft, with 2 connectors'), 1)
            notes[("DEVICES", "Direct connection: junction box at rough-in (%s)" % c.get("key"))] = (
                "Disconnect within sight or a lockable breaker per NEC - VERIFY")
        else:
            key = ("DEVICES", "Receptacle for %s, %s V %s-ph, %s A circuit - NEMA VERIFY" % (
                c.get("key"), c.get("volts"), c.get("phase"), brk))
            _add(items, key, 1)
            notes[key] = "Plug type not in the catalog: check the data plate"
    # EMT and fittings: one run per circuit, as estimated
    for size, lf in sorted(emt_lf.items()):
        _add(items, ("CONDUIT", "EMT %s, 10 ft lengths (LF)" % size), math.ceil(lf))
    for c in takeoff["circuits"]:
        run = float(c.get("home_run_lf") or 0)
        size = emt_size(circuit_plan(c)[3])
        _add(items, ("CONDUIT", "EMT %s coupling, set-screw" % size), math.ceil(run / 10.0))
        _add(items, ("CONDUIT", "EMT %s connector, set-screw" % size), 2)
        _add(items, ("CONDUIT", "EMT %s 1-hole strap" % size), math.ceil(run / 10.0) + 1)
        _add(items, ("CONDUIT", "EMT %s 90 deg sweep" % size), 2)
    es = store.get("electrical_service") or {}
    if es.get("new_subpanel"):
        amps = takeoff["quantities"]["electrical"].get("connected_amps_208_3ph")
        k = ("SUBPANEL & FEEDER", "Panelboard, 208Y/120 V 3-ph 4-wire, 42-circuit, with ground bar")
        _add(items, k, 1)
        notes[k] = "Size the main / feeder from the load calculation (connected load about %s A at 208 V 3-ph)" % (
            round(amps) if amps else "?")
        lf = float(es.get("feeder_lf") or 0)
        for col in ("BLACK", "RED", "BLUE", "WHITE"):
            k = ("SUBPANEL & FEEDER", "Feeder conductor, %s, size per load calc (LF)" % col)
            _add(items, k, math.ceil(lf * WASTE + 2 * TAIL_FT))
        k = ("SUBPANEL & FEEDER", "Feeder ground, GREEN, size per feeder breaker (LF)")
        _add(items, k, math.ceil(lf * WASTE + 2 * TAIL_FT))
        k = ("SUBPANEL & FEEDER", "Feeder conduit, size per conductors (LF)")
        _add(items, k, math.ceil(lf))
        _add(items, ("SUBPANEL & FEEDER", "Feeder breaker, 3-pole, at the existing panel - size per load calc"), 1)
    hv = store.get("hvac") or {}
    if (hv.get("system_cost") or hv.get("itemized")) and not hv.get("none"):
        lf = float(hv.get("condenser_circuit_lf") or 0)
        n = int(hv.get("condensers") or 1)
        src = ((es.get("sources") or {}).get(hv.get("condenser_source")) or {}).get("short")
        _add(items, ("HVAC CIRCUIT", "Breaker, 2-pole, for the condensing unit%s - size per unit MOCP" % (
            " (%s)" % src if src else "")), n)
        k = ("HVAC CIRCUIT", "Disconnect, fused or non-fused per unit nameplate, at the condensing unit")
        _add(items, k, n)
        if hv.get("itemized"):
            notes[k] = "In the HVAC order (non-fused, one per unit) - do not buy twice"
        _add(items, ("HVAC CIRCUIT", "Condensing unit circuit conductors, 2 + ground, size per unit MCA (LF)"),
             math.ceil(lf * WASTE + 2 * TAIL_FT) * 3 * n)
        _add(items, ("HVAC CIRCUIT", "Condensing unit circuit conduit (LF)"), math.ceil(lf) * n)
        _add(items, ("HVAC CIRCUIT", 'Whip, 3/4" liquidtight, 6 ft, disconnect to unit'), n)
    return items, notes, wire, runs


def _scope_items(store, takeoff):
    only = [str(i) for i in ((store.get("scope") or {}).get("plumbing_items") or [])]
    if only:
        return only
    return sorted((takeoff["quantities"].get("plumbing_by_item") or {}).keys())


def plumbing(takeoff, store, conns):
    """Plumbing parts for the items in the plumbing scope: from the connection list when the Revit build wrote
    one (sizes as on QF103), else from the takeoff counts."""
    scope = set(_scope_items(store, takeoff))
    by_item = takeoff["quantities"].get("plumbing_by_item") or {}
    rows = [c for c in conns if c.get("svc") in ("H", "C", "D", "I") and str(c.get("item")) in scope]
    if not rows:                        # no Revit connection list yet: rebuild from the takeoff counts
        for item in sorted(scope):
            q = by_item.get(item) or {}
            for k, svc in (("hw", "H"), ("cw", "C"), ("waste", "D"), ("indirect", "I")):
                for _ in range(int(q.get(k) or 0)):
                    rows.append({"svc": svc, "item": item, "size": "VERIFY", "tag": "%s%s" % (svc, item)})
    items, notes = {}, {}
    supplies = {}
    for r in rows:
        supplies.setdefault(str(r["item"]), []).append(r)
    for item, rs in sorted(supplies.items()):
        water = [r for r in rs if r["svc"] in ("H", "C")]
        lf_total = float((by_item.get(item) or {}).get("supply_lf") or 0)
        for r in water:
            size = (r.get("size") or "VERIFY").replace("VERIFY", "size VERIFY")
            svc = "hot" if r["svc"] == "H" else "cold"
            _add(items, ("WATER", "Stop valve, quarter-turn, %s" % size), 1)
            _add(items, ("WATER", "Drop-ear elbow / stub-out, %s" % size), 1)
            _add(items, ("WATER", "Escutcheon, %s" % size), 1)
            _add(items, ("WATER", "Supply connector, braided stainless, %s" % size), 1)
            lf = math.ceil(lf_total / len(water)) if water else 0
            _add(items, ("WATER", "Supply piping, %s, %s water (LF)" % (size, svc)), lf)
            if r["svc"] == "H":
                _add(items, ("WATER", "Pipe insulation, %s (LF)" % size), lf)
        for r in rs:
            if r["svc"] == "D":
                size = (r.get("size") or "VERIFY").replace("VERIFY", "size VERIFY")
                _add(items, ("WASTE", "P-trap with cleanout, %s" % size), 1)
                _add(items, ("WASTE", "Trap adapter and wall flange, %s" % size), 1)
                k = ("WASTE", "Waste pipe and fittings to the existing drain, %s (allowance, LF)" % size)
                _add(items, k, 5)
                notes[k] = "Plus the vent tie-in fittings the drain needs - VERIFY at site"
            elif r["svc"] == "I":
                k = ("WASTE", "Air gap fitting for indirect waste (item %s)" % item)
                _add(items, k, 1)
                notes[k] = "Size per the equipment drain - VERIFY"
                _add(items, ("WASTE", "Indirect drain line to the existing drain (allowance, LF)"), 10)
    return items, notes, rows


def notes_for(takeoff):
    """The field notes of the catalog items on the job (the dish machine's water, drain and power)."""
    items = load_catalog()["items"]
    keys = sorted(set(r["key"] for r in (takeoff or {}).get("schedule", [])))
    return [items[k]["field_note"] for k in keys if (items.get(k) or {}).get("field_note")]


def _book(path, title, store, elec, plumb, conns, takeoff=None):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    bold = Font(bold=True)
    head = PatternFill("solid", fgColor="D9D9D9")
    wb = Workbook()

    def sheet(ws, heading, cols, widths):
        ws.append([title])
        ws["A1"].font = Font(bold=True, size=13)
        ws.append([heading])
        ws.append([])
        ws.append(cols)
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
            ws.cell(row=4, column=i).font = bold
            ws.cell(row=4, column=i).fill = head
        ws.freeze_panes = "A5"

    def groups(ws, items, notes, unit_for):
        last = None
        for (grp, desc), qty in sorted(items.items(), key=lambda kv: (ORDER.index(kv[0][0]) if kv[0][0] in ORDER
                                                                       else 99, kv[0][1])):
            if grp != last:
                ws.append([grp])
                ws.cell(row=ws.max_row, column=1).font = bold
                last = grp
            ws.append(["", desc, int(math.ceil(qty)), unit_for(desc), notes.get((grp, desc), "")])

    def unit_for(desc):
        return "LF" if desc.endswith("LF)") else "EA"

    items, notes, wire, runs = elec
    ws = wb.active
    ws.title = "Electrical"
    sheet(ws, "ELECTRICAL ROUGH-IN - %d circuits" % runs, ["GROUP", "DESCRIPTION", "QTY", "UNIT", "NOTE"],
          [18, 70, 8, 6, 70])
    groups(ws, items, notes, unit_for)
    ws.append(["WIRE"])
    ws.cell(row=ws.max_row, column=1).font = bold
    for (awg, col), lf in sorted(wire.items(), key=lambda kv: (int(kv[0][0]) if kv[0][0].isdigit() else 99,
                                                                ["BLACK", "RED", "BLUE", "WHITE", "GREEN"].index(
                                                                    kv[0][1]))):
        ws.append(["", "Wire, #%s THHN/THWN-2 copper, %s" % (awg, col), int(math.ceil(lf / 10.0) * 10), "LF",
                   "Ground" if col == "GREEN" else ""])

    pitems, pnotes, prow = plumb
    ws = wb.create_sheet("Plumbing")
    scope = ", ".join("item %s" % i for i in _scope_items(store, {"quantities": {}}) or [])
    sheet(ws, "PLUMBING ROUGH-IN - %s" % (scope or "all plumbed items"), ["GROUP", "DESCRIPTION", "QTY", "UNIT",
                                                                        "NOTE"], [18, 70, 8, 6, 70])
    groups(ws, pitems, pnotes, unit_for)

    ws = wb.create_sheet("Connections")
    sheet(ws, "CONNECTIONS - tag, size and rough-in height A.F.F. (as on QF103 and the QF405 rough-in elevations)",
          ["TAG", "ITEM", "SERVICE TO", "DESCRIPTION", "SIZE", "V", "PH", "AMPS", "A.F.F.", "LOC.", "REMARKS"],
          [10, 7, 30, 42, 14, 6, 5, 7, 10, 10, 42])
    for c in conns:
        ws.append([c.get("tag"), c.get("item"), c.get("service_to"), c.get("description"), c.get("size"),
                   c.get("v"), c.get("ph"), c.get("amps"), c.get("aff"), c.get("loc"), c.get("remarks")])
    if not conns:
        ws.append(["(Run the Revit build to list every connection with its height.)"])

    ws = wb.create_sheet("Notes")
    ws.column_dimensions["A"].width = 140
    ws.append([title])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([])
    ws.append(["HOW THE QUANTITIES ARE COUNTED"])
    ws["A3"].font = bold
    for r in RULES:
        ws.append(["- " + r])
    ws.append([])
    ws.append(["VERIFY BEFORE ORDERING"])
    ws.cell(row=ws.max_row, column=1).font = bold
    for v in VERIFY + notes_for(takeoff) + list(store.get("material_verify") or []):
        ws.append(["- " + v])
    for row in ws.iter_rows(min_row=4):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    wb.save(path)


ORDER = ["DEVICES", "BOXES & PLATES", "BREAKERS", "CONDUIT", "SUBPANEL & FEEDER", "HVAC CIRCUIT", "WATER", "WASTE"]


def run(store_id, work, out=None):
    store = _load(os.path.join(ROOT, "config", "stores", store_id + ".json"))
    takeoff = _load(os.path.join(work, "takeoff.json"))
    cpath = os.path.join(work, "_AEQ OUTPUT", "roughin_connections.json")
    conns = _load(cpath) if os.path.isfile(cpath) else []
    rev = store.get("drawing_rev") or "R0"
    name = store.get("set_name") or os.path.basename(store.get("drive_folder") or store_id)
    title = "%s - ROUGH-IN MATERIAL LIST %s (%s)" % (name, rev, store_id)
    elec = electrical(takeoff, store)
    plumb = plumbing(takeoff, store, conns)
    path = os.path.join(out or work, "%s-ROUGH-IN MATERIAL LIST-%s.xlsx" % (name, rev))
    _book(path, title, store, elec, plumb, conns, takeoff)
    return path, elec, plumb, conns


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("store_id")
    ap.add_argument("--work", required=True, help="the store's work folder (takeoff.json, _AEQ OUTPUT)")
    ap.add_argument("--out", help="folder for the workbook (default: the work folder)")
    a = ap.parse_args()
    p, elec, plumb, conns = run(a.store_id, a.work, a.out)
    print(p)
    print("electrical lines %d, wire sizes %d, plumbing lines %d, connections %d" % (
        len(elec[0]), len(elec[2]), len(plumb[0]), len(conns)))
