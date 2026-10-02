# PH / Cinemark Retrofit: Revit drawings, rough-ins and construction quotes

Program: **SSG-2026-CNK-PH01**, Tier 2 (Full Design & Rough-In Package). This repo turns a store survey
layout (DXF) into:

1. **Drawings** (Revit 2026, AEQ 11x17 foodservice template): demolition plan, equipment plan + schedule,
   electrical and plumbing rough-in plans with callouts, electrical rough-in schedule, interior elevations,
   3D view, a combined PDF sheet set and a 3D image.
2. **Construction quote** (Spirit Services Group): demolition, construction & finishes, electrical,
   plumbing, HVAC (sized from a heat load), equipment set & start-up, general conditions and travel, as a
   customer PDF in the HELM quote format plus an **INTERNAL** cost workbook.
3. **Timeline**: a working-day schedule and Gantt chart driven by the estimated labor hours.

The drawings and the quote both read the same `takeoff.json`, so circuit counts, home-run lengths,
connections and tonnage tie out between the rough-in sheets and the price.

```
config/            program.json (customer, contractor, rates, defaults), families.json (equipment catalog),
                   cost_db.json (INTERNAL unit assemblies), stores/<id>.json (one per theatre)
tools/             dxf_extract.py  DXF -> layout.json
                   takeoff.py      layout -> rough-in schedule, quantities, heat load
                   run_store.py    one command per store (everything below)
estimating/        estimate.py, schedule.py, build_quote.py, quote_doc.py
revit/AEQ.extension  pyRevit ribbon tab "AEQ" > panel "Cinemark PH" (Revit 2026)
docs/              DXF_STANDARD.md, REVIT_WORKFLOW.md, ESTIMATING.md
tests/             pytest: GA 263 heat-load calibration, DXF extraction, estimate, customer-doc leak check
stores/<id>/       outputs (gitignored; publish to Drive)
```

## Quick start (any machine with Python 3.10+)

```
pip install -r requirements.txt
python tools/run_store.py GA-263                      # package-list mode (no DXF needed)
python tools/run_store.py TX-093 --dxf "G:/My Drive/CINEMARK/PIZZA HUT/TX 093 MCALLEN HOLLYWOOD/TX 093 MCALLEN HOLLYWOOD.dxf"
python -m pytest -q tests
```

On the Revit PC, follow `docs/REVIT_WORKFLOW.md`.

## Stores

| Store | Theatre | Status |
|---|---|---|
| GA-263 | Tinseltown USA, 134 Pavilion Pkwy, Fayetteville GA | QF101-R1 + heat load issued; calibration store |
| TX-093 | #93 (list: Movies 17), 100 W. Nolana Loop, McAllen TX | needs DXF; confirm theatre name |
| NJ-187 | #187 (list: Cinemark 16, Cooper Towne Center, **Somerdale** NJ) | needs DXF; Drive folder says "Somerset", confirm |

## Status

- CPython pipeline: built and tested here (5 tests). The GA 263 heat load reproduces the issued report
  SSG-2026-CNK-PH01-263-M1 within 0.05% (61,081 vs 61,106 Btu/h design; 5.09 tons; 2 x 2.5-ton).
- Revit extension: written against the Revit 2026 API but **not yet run in Revit**. Expect a short
  first-run shakedown on the PC (checklist in `docs/REVIT_WORKFLOW.md`).
- Pricing: assemblies marked `HELM:` reproduce pricing bases on file; everything marked
  `AEQ est - review` needs Edgar's numbers before a quote is issued.
