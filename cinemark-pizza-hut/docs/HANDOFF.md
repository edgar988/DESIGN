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
- pyRevit extension `revit/AEQ.extension` (buttons Settings, 1-7): written, **never run in Revit**.
  First job here: run GA-263 through it and fix `revit/AEQ.extension/lib/aeq_cinemark/build.py` until clean.
  You can drive Revit headlessly with `pyrevit run <script> <model> --revit=2026` (see `revit/batch/build_store.py`).
- `desktop/setup_desktop.ps1`: installs Git/Python/pyRevit, pip packages, registers the extension and an
  optional DXF watcher task. Run it (or do the equivalent steps) to set this machine up.

## Waiting on Edgar
- DXF exports (AutoCAD SAVEAS DXF 2018) of each store layout into its Drive store folder:
  `GA 263 FAYETTEVILLE TINSELTOWN`, `TX 093 MCALLEN HOLLYWOOD`, `NJ 187 SOMERSET CINEMARK`. Only DWGs exist
  now. If AutoCAD is on this machine you may be able to export them yourself (`accoreconsole.exe` script
  with `-DXFOUT` / `SAVEAS`) - ask Edgar before writing into his DWG folders.
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

## Where things go
- Quote PDFs: Drive `AARON EQUIPMENT CO SHARED DRIVE\QUOTES\2026`; INTERNAL workbooks: `QUOTES\_RECORDS`.
- Drawings/PDF sets: the store's Drive folder. Revit models: local `C:\AEQ\work\<store>`.
- Branch: `cinemark-ph-revit-pipeline` on `edgar988/DESIGN`. Commit with tests passing; push.
