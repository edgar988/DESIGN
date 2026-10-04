# Revit PC workflow (Revit 2027 + pyRevit)

## One-time setup

1. Install **pyRevit** (6.5+ for Revit 2027; not on winget, use the signed installer from GitHub releases)
   and **Python 3.12** (python.org), then
   `pip install -r requirements.txt` from this folder.
2. Clone `edgar988/design` (e.g. `C:\AEQ\design`). Register the extension folder:
   `pyrevit extensions paths add "C:\AEQ\design\cinemark-pizza-hut\revit"` (or pyRevit Settings >
   Custom extension directories), then reload pyRevit. `desktop/setup_desktop.ps1` does all of this
   (see `docs/DESKTOP_SETUP.md`).
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
| 5 Views & Rough-In | frames every template QF plan (QF101..QF402) on the room at 1/4" (steps down only if the room overflows the sheet; the AEQ view templates own the scale, so it is set on them in the store model); each rough-in becomes a numbered utility (E1.. per circuit, P1.. per pipe service): a small Generic Model marker on the wall at its A.F.F. carrying the template's AEQ Utility parameters, so the QF103 Combined Utility Schedule fills itself, and only the tag numbers on QF302 / QF202; four interior elevations (QF403); QF502 3D (realistic, section box, walls 50% transparent); QF102 uses the template's equipment schedule |
| 6 Sheets | lays the views onto the template's QF sheets (re-centred in the drawing area left of the title strip) and adds QF403 INTERIOR ELEVATIONS and QF502 3D VIEW |
| 7 Export PDF & 3D | combined QF set PDF + 3600 px realistic 3D PNG to the store output folder |

Then publish the PDF/PNG/quote to the store's Drive folder and the quote to `QUOTES/2026`.

## First run: GA-263, 2026-10-04 (Revit 2027.0.4, pyRevit 6.5.5, headless `pyrevit run`)

Runs clean end to end: model, 13 walls, 4 families placed, KCL data synced, 17-sheet QF set PDF, 3D PNG.
- [x] Wall types: interior partition types are preferred (ties go thicker). The AutoQuotes 4" face pairs
      have no 4" interior type, so they use Interior 4 7/8" and are reported as a mismatch.
- [x] Alignment: equipment bbox centres land on the DXF blocks (checked hand sink, Hobart, ACP).
- [x] Elevations are named from each view's actual direction, cropped to the room, phase New Construction.
- [x] Title block: the template's own (AEQ_TITLEBLOCK_11X17_2026); drawing area `sheet_area_in` in config.
- [x] Phases: template uses "Existing" / "New Construction". Template QF views keep their own phase; QF111's
      AEQ view template phase filter currently shows new work.
- [ ] Hosting/height: the 7-PS-65 hand sink family is level-based and lands on the floor (connectors read
      -1.7" / -6.4" AFF). Needs a mounting height per catalog entry.
- [x] Item numbers: read from the AutoQuotes data in the DWG (Marks, QF102 ITEM, callouts).
- [x] Orientation follows the drawing's walls: an item backs onto the wall behind it and faces into the room
      (`revit_rotation` from dxf_extract); free-standing items keep the drawn rotation (corrected for AQ
      blocks drawn front-to-+X). Block axis conventions differ between AQ and KCL, the wall does not.
- [x] Rough-ins: numbered tags only on the plans (E1/E2, P1/P2/P3 at one fixture), outside the room in wall
      order; details in the QF103 schedule. Items without a family still get rough-ins.
- [ ] Elevation tags: the utility markers show at their A.F.F. in QF403 but carry no tag text yet.
- [ ] REMARKS on QF102 shows the catalog key (the build keeps the key in instance Comments).
- [ ] The "photoreal render" is a realistic-style 3D export. Revit's API cannot drive the renderer; for a
      true render use Revit Render / Enscape on the QF502 view (saved camera + section box are set up).

Report anything that breaks: the fix goes into `lib/aeq_cinemark/build.py` once and holds for all stores.
