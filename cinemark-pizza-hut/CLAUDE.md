# Working rules for this folder (PH / Cinemark retrofit)

- User is **Edgar Pendley** (never "Edgar Aaron"). Cinemark construction runs under **Spirit Services Group, LLC**.
- Internal cost, margin, hours, rates and travel pad NEVER go on customer documents or emails.
  `tests/test_pipeline.py::test_customer_outputs_carry_no_internal_numbers` enforces this. Keep it passing.
- Never guess part numbers or utilities. Catalog entries are `verified: true` only with a named
  manufacturer source; unknowns stay `null`/`VERIFY` until synced from the KCL family or confirmed.
- 208V is commercial (default); a data plate "3/1" means the unit accepts either, not that the site has 3-phase.
- After-hours or weekend work is always billed.
- Emails are drafts only; no em dashes in emails. Edgar sends.
- Run `python -m pytest -q tests` before committing. GA-263 must keep reproducing its issued heat load.

## On the Revit desktop (C:\AEQ\design)
- Edgar works in Revit/AutoCAD/Inventor/Fusion while you run. Never close, restart or drive those apps
  unless asked; run the pipeline as background processes. Revit models live in C:\AEQ\work.
- Store outputs: `<Drive store folder>\_AEQ OUTPUT\` (watcher) or `C:\AEQ\work\<store>` (ribbon).
- Settings: `%APPDATA%\pyRevit\aeq_cinemark.json`. Watcher log: `%LOCALAPPDATA%\AEQ\watch_stores.log`.
