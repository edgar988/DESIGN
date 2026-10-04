# Running live on the Revit desktop (without interrupting your work)

The desktop runs Claude, Revit 2026, Inventor, AutoCAD and Fusion. This setup keeps the
pipeline out of their way:

| Piece | When it runs | How it stays out of the way |
|---|---|---|
| **Watcher** (`tools/watch_stores.py`) | starts at logon, checks every 60 s | hidden (pythonw), below-normal CPU priority, no windows; a tray balloon (no focus steal) when a quote updates |
| **Takeoff / quote / schedule** | when a store DXF changes and has stopped changing | runs as a low-priority background process; writes only to `<store folder>\_AEQ OUTPUT\` |
| **Revit drawings, interactive** | when you click the AEQ ribbon buttons | your choice, in your Revit |
| **Revit drawings, background** (optional, `-AutoRevit`) | after 10 min with no keyboard/mouse input | a **separate** Revit instance via `pyrevit run`; your open Revit is never touched; the model stays in `C:\AEQ\work` (never on the Drive stream); PDF + 3D image are copied to `_AEQ OUTPUT` |

## Setup (once, about 10 minutes)

1. Make sure **Google Drive for Desktop** is signed in and `CINEMARK\PIZZA HUT` is visible in Explorer.
2. Download `desktop/setup_desktop.ps1` from GitHub (branch `cinemark-ph-revit-pipeline`) and run it in
   PowerShell (not admin):
   ```
   powershell -ExecutionPolicy Bypass -File "$HOME\Downloads\setup_desktop.ps1"
   ```
   It installs Git / Python 3.12 / pyRevit if missing, clones the repo into `C:\AEQ\design`, installs the
   Python packages, runs the tests, registers the AEQ ribbon, finds the Drive folder, writes the settings
   and starts the watcher. Re-run it any time to update.
3. Next time you open Revit (or pyRevit > Reload) the **AEQ > Cinemark PH** tab is there.

## Day to day

- In AutoCAD, `SAVEAS` the store layout as **DXF 2018** into its Drive store folder
  (layer rules: `docs/DXF_STANDARD.md`). About two minutes later `_AEQ OUTPUT` has the quote PDF,
  INTERNAL workbook, Gantt and `STATUS.txt`. Save again and it re-runs.
- Drawings: AEQ ribbon buttons 2-7 when convenient. Once GA-263 has been through them cleanly,
  re-run setup with `-AutoRevit` and the drawings rebuild themselves while you are away from the desk.
- Pause everything: Task Scheduler > **AEQ Cinemark Watcher** > Disable. Log:
  `%LOCALAPPDATA%\AEQ\watch_stores.log`.

## Claude on the desktop

Open the Claude desktop app > **Code** and choose the folder `C:\AEQ\design`. That session works
directly on this machine: it can run a store (`python tools\run_store.py TX-093 --dxf ...`), read the
outputs in `_AEQ OUTPUT`, adjust pricing in `config\cost_db.json` or a store's `overrides`, fix the Revit
library after a first-run error, and commit/push. `CLAUDE.md` in `cinemark-pizza-hut\` gives it the
HELM rules. It does not need Revit closed for any of that.

## Notes

- The `_AEQ OUTPUT` folder holds the **INTERNAL** workbook. Keep the CINEMARK Drive folder internal;
  share only the customer PDF.
- A background Revit build uses its own Revit process (memory ~2-4 GB) under your same Autodesk
  sign-in; it only starts when the machine is idle and closes when done.
- The `pyrevit run` build has run clean on GA-263 (Revit 2027). Not yet run on Windows: the scheduled
  task, idle detection and tray notice. The pipeline itself is tested.
