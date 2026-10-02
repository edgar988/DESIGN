# Revit PC workflow (Revit 2026 + pyRevit)

## One-time setup

1. Install **pyRevit** (5.x, supports Revit 2026) and **Python 3.12** (python.org), then
   `pip install -r requirements.txt` from this folder.
2. Clone `edgar988/design` (e.g. `C:\AEQ\design`). Register the extension:
   `pyrevit extend ui AEQ "C:\AEQ\design\cinemark-pizza-hut\revit"` (or pyRevit Settings > Custom
   extension directories > add `...\cinemark-pizza-hut\revit`), then reload pyRevit.
3. Make sure Google Drive for Desktop syncs `CINEMARK\PIZZA HUT` (families, template, store folders).
4. Ribbon **AEQ > Cinemark PH > Settings**: pick the store, confirm the Drive folder, the CAD TEMPLATES
   folder, `AEQ_FOODSERVICE_11X17_2026.rte`, the revision, and the Python 3 path.

## Per store

| Button | What it does |
|---|---|
| 1 Takeoff & Quote | runs `tools/run_store.py`: DXF -> layout, takeoff, heat load, estimate, schedule, customer PDF + INTERNAL xlsx |
| 2 New Model | new `<store>_PH.rvt` from the 11x17 template, Project Information filled |
| 3 Walls & Underlay | links the DXF at origin; builds existing / demo / new walls by phase |
| 4 Place Equipment | loads each family from CAD TEMPLATES, places at the DXF block (rotation + bbox-centre alignment), Mark = item; writes `config/families_synced.json` from the KCL parameters and connectors; offers to re-run 1 |
| 5 Views & Rough-In | FS-001 demo, FS-101 equipment, FS-201 electrical, FS-301 plumbing plans (equipment halftoned, callouts with leaders), FS-202 rough-in schedule, four interior elevations, FS-501 3D (realistic, section box), equipment schedule |
| 6 Sheets | FS-001..FS-501 on the template title block with views placed |
| 7 Export PDF & 3D | combined sheet-set PDF + 3600 px realistic 3D PNG to the store output folder |

Then publish the PDF/PNG/quote to the store's Drive folder and the quote to `QUOTES/2026`.

## First run (the Revit code has not been executed yet)

Run GA-263 first: its layout is known and QF101-R1 exists to compare against. Check:
- [ ] Wall types: the template needs basic wall types near the surveyed thicknesses (3/4/5/6/8 in). The
      button reports which thicknesses fell back to the closest type.
- [ ] Hosting: wall-hosted families (hand sink, wall shelf) fall back to the nearest wall; confirm side/height.
- [ ] Alignment: equipment is moved so its plan bbox centre matches the DXF block's. Check one item against
      the underlay; if the KCL family includes clearance zones in its bbox, set `"align": "insert"` (future).
- [ ] Elevation marker index -> direction naming (NORTH/WEST/SOUTH/EAST) in `interior_elevations`.
- [ ] Title block placement offsets in `make_sheets` for the 11x17 sheet; tune once, then it repeats.
- [ ] The "photoreal render" is a realistic-style 3D export. Revit's API cannot drive the renderer; for a
      true render use Revit Render / Enscape on the FS-501 view (saved camera + section box are set up).
- [ ] Phase names: template must use "Existing" / "New Construction" (falls back to first / last phase).

Report anything that breaks: the fix goes into `lib/aeq_cinemark/build.py` once and holds for all stores.
