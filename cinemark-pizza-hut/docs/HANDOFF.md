# Handoff: cloud session -> local session on the Revit desktop (2026-10-04)

Read this, `CLAUDE.md`, `README.md` and `docs/REVIT_WORKFLOW.md` first. This machine is a dedicated
workstation Edgar watches over a remote window; he does not use the keyboard/mouse on it. You run here.

## Goal
Cinemark Pizza Hut kitchen conversions (program SSG-2026-CNK-PH01, Tier 2), three surveyed stores:
1. Revit 2026 drawings from each store's DXF layout: equipment plan, electrical + plumbing rough-ins,
   elevations, 3D, on the AEQ 11x17 template, using the KCL families in Drive `CINEMARK\PIZZA HUT\CAD TEMPLATES`.
2. Firm construction quote per store (demo, construction, electrical, plumbing, HVAC, set-up, travel)
   + timeline, Spirit Services Group, HELM quote standards.

## State
- Python pipeline (DXF -> layout -> takeoff -> heat load -> estimate -> schedule -> quote PDF + INTERNAL xlsx):
  built, 7 tests pass. GA-263 reproduces its issued heat-load report.
- 2026-10-04 (local session on the desktop): machine set up (Git, Python 3.12, pyRevit 6.5.5 on **Revit 2027**,
  extension registered, settings at `%APPDATA%\pyRevit\aeq_cinemark.json` -> Drive
  `G:\Shared drives\AARON EQUIPMENT CO SHARED DRIVE\CINEMARK\PIZZA HUT`). Watcher task NOT registered yet.
- GA-263 DXF exported with AutoCAD 2027 accoreconsole (DXFOUT 2018) into the store's Drive folder.
- Revit build runs clean on GA-263 headless (`pyrevit run revit/batch/build_store.py --revit=2027`, ~65 s):
  builds into the template's QF sheet set at 1/4" (Edgar's call), adds QF403 elevations + QF502 3D.
  Model + outputs in `C:\AEQ\work\GA-263`. Open items in `docs/REVIT_WORKFLOW.md` "First run".
- `desktop/setup_desktop.ps1`: installs Git/Python/pyRevit, pip packages, registers the extension and an
  optional DXF watcher task.

## Waiting on Edgar
- DXF exports (AutoCAD SAVEAS DXF 2018) of each store layout into its Drive store folder:
  `GA 263 FAYETTEVILLE TINSELTOWN`, `TX 093 MCALLEN HOLLYWOOD`, `NJ 187 SOMERSET CINEMARK`. GA 263 done
  (Edgar OK'd exporting with AutoCAD here: copy the DWG to C:\AEQ\work, `accoreconsole.exe /i <dwg> /s <scr>`
  with `FILEDIA 0` / `_.DXFOUT "<dxf>" _V 2018 16` / `_.QUIT _Y`, copy the DXF to the store folder).
- Pricing review of every `AEQ est - review` assembly in `config/cost_db.json` (HELM-anchored ones are set).
- SSG + AEQ logo PNGs into `estimating/assets/` (ssg_logo.png, aeq_logo.png).

## Open flags (do not resolve by guessing)
- Store #187: Cinemark list says Cinemark 16, Cooper Towne Center, **Somerdale** NJ; Drive folder says Somerset.
- Store #93: list says "Movies 17", 100 W. Nolana Loop, McAllen; folder says Hollywood.
- Ovention C2000 and Hobart LXnR families are 208/1 types; GA specs are 208/3. Confirm site phase per store.
- GA package items without .rfa: Beverage-Air DP60HC / WTF60AHC, True STR1R / STR2F / STA1HRI, MTI AutoFry, KLG-365, WS-12-108.
- Unverified utilities: 7-PS-65 hand sink, FC-3 sink, Hoshizaki, Traulsen, PerfectFry, Follett. Button 4 syncs
  them from the KCL families into `config/families_synced.json`.
- Licensing: no AEQ TX mechanical license (McAllen HVAC); NJ needs licensed plumbing/electrical of record.
- GA-263 test quote came to $141,000 (travel $31.7k, 628 field hours). Draft only, not issued.
- GA-263 drawing (AQ data) vs the store package, to review with Edgar: items 6 ACP and 7 Ovention each drawn
  twice as identical stacked blocks, items 10 KLG-365 and 11 STR1R drawn twice (package qty 1), package item
  15 MTI AutoFry not drawn, drawing item 16 Broaster VF-3 not in the package, items 3/4 T&S faucets not in the
  catalog. Priced from the DXF as drawn: $181,600, design heat 89,053 Btu/h (the package-list path still
  reproduces the issued 61,106). No panel / water / waste source points or room boundary on the drawing.
- Stores are reviewed one at a time with Edgar (AutoQuotes is on the desktop to correct the drawings).
- 7-PS-65 hand sink lands on the floor in Revit (level-based family); needs a mounting height.

## Where things go
- Quote PDFs: Drive `AARON EQUIPMENT CO SHARED DRIVE\QUOTES\2026`; INTERNAL workbooks: `QUOTES\_RECORDS`.
- Drawings/PDF sets: the store's Drive folder. Revit models: local `C:\AEQ\work\<store>`.
- Branch: `cinemark-ph-revit-pipeline` on `edgar988/DESIGN`. Commit with tests passing; push.
