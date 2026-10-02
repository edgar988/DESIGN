# Estimating: how the store quote is built

**INTERNAL.** Cost, margin, hours and travel never appear on customer documents
(a test fails if they do).

- **Quantities** come from `takeoff.json` (DXF geometry + equipment catalog), never typed by hand.
- **Unit assemblies** (`config/cost_db.json`): hours + material + sub + equipment per unit.
  Sell = hrs x $95 + material x 1.30 + sub x 1.20 + equipment x 1.10; true cost uses $42/hr.
  Lines marked `HELM:` reproduce pricing bases on file to the dollar ($25/LF branch circuits, QOB120 $95,
  QOB250 $217.50, 5-20R $125, 14-50R $271.25, removal $150/machine, set & commission $750 plug-in /
  $875 plumbed). Lines marked `AEQ est - review` are starting points for Edgar to set.
- **HVAC** is sized by the heat load (ASHRAE Ch.18 duty-factor method, same as report
  SSG-2026-CNK-PH01-263-M1): 2.5-ton steps, nominal within 3% accepted, two-unit staging.
- **Travel**: drive <= 450 mi = home weekends (weekly round trips, lodging Mon-Thu); farther or fly =
  rotations every two weeks with weekend stays. Travel labor billed at the standard rate.
- **Contingency** 7% is spread into the sections; totals round up to $100.
- **After-hours / weekend** windows (store `schedule.work_window`) bill labor at +50%. Always bill for it.
- **Overrides** (Edgar's numbers win) in the store file:
  `"overrides": {"section_sell": {"HVAC": 19500}, "total_sell": 128000, "round_to": 500}`.
- **Numbering**: `SSG-2026-CNK-PH01-<theatre>-C1` (heat load is `-M1`), revisions R0, R1...
  File: `26_GA_CINEMARK_263-FAYETTEVILLE-TINSELTOWN_PH-CONSTRUCTION_SSG-2026-CNK-PH01-263-C1_R0_10-02-26.pdf`.
- **Equipment** is owner-furnished (program proposal). If AEQ supplies any, add one line per vendor quote
  item at cost x 1.10 (HELM rule).
- **Licensing**: TX has no AEQ mechanical license yet (sub the HVAC or license before work); NJ needs a
  licensed plumbing/electrical contractor of record.
