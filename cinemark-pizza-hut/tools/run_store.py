#!/usr/bin/env python3
"""One command per store: DXF -> layout -> takeoff -> estimate -> schedule -> quote PDF + INTERNAL xlsx.

    python tools/run_store.py GA-263                 # uses stores/GA-263/<layout_dxf> if present, else package list
    python tools/run_store.py TX-093 --dxf "path/to/TX 093 MCALLEN HOLLYWOOD.dxf"
    python tools/run_store.py NJ-187 --rev R1 --ntp 2026-11-02

Outputs land in stores/<id>/ (gitignored): layout.json, takeoff.json, estimate.json,
<quote>.pdf (customer), <quote>_INTERNAL.xlsx, schedule.png, roughin_electrical.csv.
The Revit extension reads layout.json and takeoff.json from the same folder.
"""
import argparse
import csv
import datetime as dt
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tools import dxf_extract, takeoff as tk          # noqa: E402
from estimating import estimate as est_mod, schedule as sch_mod, build_quote as bq   # noqa: E402


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _json_default(o):
    if isinstance(o, (dt.date, dt.datetime)):
        return o.isoformat()
    raise TypeError(type(o))


def run(store_id, dxf=None, rev="R0", ntp=None, outdir=None, today=None):
    store = load(os.path.join(ROOT, "config", "stores", store_id + ".json"))
    program = load(os.path.join(ROOT, "config", "program.json"))
    catalog = tk.load_catalog()
    out = outdir or os.path.join(ROOT, "stores", store_id)
    os.makedirs(out, exist_ok=True)

    dxf = dxf or os.path.join(out, store.get("layout_dxf", ""))
    layout = None
    if dxf and os.path.isfile(dxf):
        layout = dxf_extract.extract(dxf, store)
        json.dump(layout, open(os.path.join(out, "layout.json"), "w"), indent=1)
        print("layout:", layout["summary"])
        for w in layout["warnings"]:
            print("  LAYOUT WARNING:", w)
    else:
        print("no DXF at %s - using the store's package list" % dxf)

    t = tk.build(store, catalog, program, layout)
    json.dump(t, open(os.path.join(out, "takeoff.json"), "w"), indent=1)
    e = est_mod.estimate(t, store, program)
    s = sch_mod.build(e, store, ntp)
    json.dump({"estimate": {k: v for k, v in e.items() if k != "sections"}, "schedule": s},
              open(os.path.join(out, "estimate.json"), "w"), indent=1, default=_json_default)

    no, fname = bq.quote_numbers(store, rev, today)
    png = sch_mod.gantt_png(s, os.path.join(out, "schedule.png"))
    pdf = bq.build_pdf(os.path.join(out, fname + ".pdf"), store, program, t, e, s, png, rev, today)
    xlsx = bq.build_internal_xlsx(os.path.join(out, fname + "_INTERNAL.xlsx"), store, t, e, s)
    with open(os.path.join(out, "roughin_electrical.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(t["circuits"][0].keys()) if t["circuits"] else ["circuit"])
        w.writeheader()
        w.writerows(t["circuits"])

    h = t["heat_load"]
    print("%s %s %s" % (store_id, no, rev))
    print("  heat: design %s Btu/h -> %d x 2.5-ton" % (format(h["design_total"], ","), h["minisplit_units"]))
    for sec in e["section_order"]:
        print("  %-28s %12s" % (sec, "${:,.0f}".format(e["customer_sections"][sec])))
    print("  %-28s %12s   (true cost ${:,.0f}, margin %.1f%%, %s)".format(e["true_cost"]) % (
        "TOTAL", "${:,.0f}".format(e["total"]), e["margin_pct"], e["markup_check"]))
    print("  schedule: on site %s, turnover %s (%d site days)" % (
        s["site_start"], s["turnover"], s["site_working_days"]))
    if t["unverified"]:
        print("  UNVERIFIED (sync family data before issuing):", ", ".join(t["unverified"]))
    print("  ->", pdf)
    print("  ->", xlsx)
    return {"pdf": pdf, "xlsx": xlsx, "takeoff": t, "estimate": e, "schedule": s}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("store_id")
    ap.add_argument("--dxf")
    ap.add_argument("--rev", default="R0")
    ap.add_argument("--ntp", help="notice to proceed YYYY-MM-DD")
    ap.add_argument("--out", help="output folder (default stores/<id>)")
    a = ap.parse_args()
    ntp = dt.date.fromisoformat(a.ntp) if a.ntp else None
    run(a.store_id, a.dxf, a.rev, ntp, a.out)


if __name__ == "__main__":
    main()
