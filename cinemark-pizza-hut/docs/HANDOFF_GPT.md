# HANDOFF: Cinemark Pizza Hut kitchen conversions (drawings + estimates redo)

Written 2026-10-05 for the next assistant (GPT). Everything you need is here or in the files it points to.
Read it all before touching anything. Owner of the work: **Edgar Pendley** (never "Edgar Aaron"),
Managing Member, Aaron Equipment Co (AEQ) / Spirit Services Group, LLC (SSG).

---

## 0. Where things stand, in one paragraph

A Python pipeline reads Edgar's AutoCAD/AutoQuotes layouts (exported to DXF), numbers the equipment from a Pizza Hut
master list, prices the construction (customer quote PDF + internal workbook) and drives Revit 2027 headless through
pyRevit to build a model and an 11x17 QF drawing set. **Edgar reviewed the 2026-10-05 drawings and rejected them:
"not even good enough to call a rough draft."** Your job: redo the entire presentation (every drawing sheet and the
estimate for each store) to the standard of a senior architect and a seasoned foodservice drafter, and save all
drawings and estimates to Google Drive in each store's own folder. Section 4 lists every defect he called out and its
root cause; section 9 is the acceptance checklist.

---

## 1. People, company, program

| | |
|---|---|
| Customer | Cinemark USA, Inc., 3900 Dallas Parkway, Plano, TX 75093. Attn: Juan Cardenas (Director of Culinary), David Haywood (Senior VP of Food & Beverage); cc Natalie Ingram |
| Contractor (construction quotes) | Spirit Services Group, LLC, 147 Old Hermitage Avenue, Nashville, TN 37210. Edgar Pendley, Managing Member. 615.622.5501, edgar@aaroneq.com. Licenses TN78453, MCN153292, VC1243, AL53577 |
| Equipment supplier | Aaron Equipment Co (AEQ), same address, aaroneq.com. Drawings carry the AEQ title block |
| Program | SSG-2026-CNK-PH01 (Tier 2). Quote no. `SSG-2026-CNK-PH01-<theatre 3 digits>-C1`, heat-load doc `...-M1` |

Stores (store config files in `config/stores/`):

| ID | Theatre | Address | Drive folder | Status |
|---|---|---|---|---|
| GA-263 | Cinemark #263 Tinseltown USA | 134 Pavilion Parkway, Fayetteville, GA 30214 | `GA 263 FAYETTEVILLE TINSELTOWN` | drawn + priced (draft) |
| TX-093 | Cinemark #93 Movies 17 (folder says Hollywood) | 100 W. Nolana Loop, McAllen, TX 78504 | `TX 093 MCALLEN HOLLYWOOD` | drawn + priced (draft) |
| NJ-187 | Somerset per folder; Cinemark list says Cinemark 16, Cooper Towne Center, **Somerdale** NJ (open flag) | | `NJ 187 SOMERSET CINEMARK` | **blocked**: the DWG has two copies of the room; Edgar has not said which is the design |

---

## 2. Hard rules (do not break)

1. **Internal numbers never reach the customer**: cost, margin, hours, labor rates, travel pad, the master price list
   prices. Customer PDF = section sells + scope text only. `tests/test_pipeline.py::test_customer_outputs_carry_no_internal_numbers`
   enforces this; keep it passing.
2. **Never guess part numbers or utilities.** Catalog entries are `verified: true` only with a named manufacturer
   source (cut sheet, KCL family data). Unknowns stay `null` / `VERIFY`.
3. 208 V is the commercial default. A data plate "3/1" means the unit accepts either; it does not mean the site has 3-phase.
4. After-hours or weekend work is always billed.
5. Emails are drafts only, never sent; no em dashes; Edgar sends them himself.
6. Run `python -m pytest -q tests` before every commit (15 tests pass now). GA-263 must keep reproducing its issued
   heat load, 61,106 Btu/h design (through `issued_package` in `GA-263.json`).
7. The drawing is the truth: **positions, rotations and walls come from Edgar's DWG exactly.** Do not "improve"
   placement with heuristics (that is what broke the last set; see 4.2).
8. Don't overwrite Edgar's DWG / DXF / PDFs in the Drive store folders. Write new files with clear names and a revision.
9. Never enter passwords or credentials anywhere. `git push` needs Edgar's GitHub sign-in: he runs it.
10. The machine is dedicated to this work: Edgar said run anything openly (Revit, AutoCAD, pyRevit headless).
11. Downloading families from KCL: Edgar said missing blocks/families "can be retrieved from KCL" (KCL CADalog,
    kclcad.com). Tell him the exact file names before downloading; if a sign-in is needed, Edgar does it.

---

## 3. Machine, tools, paths

| What | Where |
|---|---|
| OS | Windows 11 Home, workstation `OFFICECAD`, user `Edgar`. Edgar works over remote desktop |
| Repo | `C:\AEQ\design` = github.com/edgar988/DESIGN, branch `cinemark-ph-revit-pipeline`; project in `cinemark-pizza-hut\` |
| Commits | local only, not pushed (latest `40ea19d`). Push = Edgar runs `git push` in `C:\AEQ\design` |
| Python | 3.12, `C:\Users\Edgar\AppData\Local\Programs\Python\Python312\python.exe`; `pip install -r requirements.txt` done (ezdxf, openpyxl, reportlab, matplotlib, pillow, pdfplumber, pytest; numpy via matplotlib; no shapely / scipy) |
| PowerShell note | reload PATH each command: `$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')` |
| Revit | 2027 (27.0.4). pyRevit 6.5.5 (IronPython 2.7.12, .NET Core): `C:\Users\Edgar\AppData\Roaming\pyRevit-Master\bin\pyrevit.exe` |
| AEQ pyRevit extension | registered from `C:\AEQ\design\cinemark-pizza-hut\revit` (ribbon AEQ > Cinemark PH, buttons 1-7 + Settings) |
| Settings | `%APPDATA%\pyRevit\aeq_cinemark.json`: repo_root, drive_root, families_dir, template_rte, outputs_dir/work_dir `C:\AEQ\work`, python_exe, pyrevit_exe, revit_year 2027 |
| AutoCAD | 2027; headless `accoreconsole.exe` for DWG -> DXF (script below) |
| AutoQuotes | installed; Edgar uses it to edit the layouts |
| Work folders | `C:\AEQ\work\<store>\`: `src\<dxf>`, layout.json, takeoff.json, estimate.json, `<store>_PH.rvt`, QF set PDF, 3D PNG, quote PDF + INTERNAL xlsx |

Google Drive (shared drive, mounted as G:):
`G:\Shared drives\AARON EQUIPMENT CO SHARED DRIVE\CINEMARK\PIZZA HUT\`
- `CAD TEMPLATES\`: the AEQ Revit template `AEQ_FOODSERVICE_11X17_2026.rte` and the KCL families (QF_*.rfa):
  ACP MXP22TLT, Advance 7-PS-65, Advance ELAG 24x72, Advance FSS 30x96, Advance FC-3-2030-20RL, Advance TFSS 30x48,
  Hobart LXnR, Hoshizaki UF60A (stand-in for the Atosa AUF60SD), Hoshizaki EF1A-FS, Hoshizaki F2A-FS, Hoshizaki UR48B,
  Follett SG1650S-60, Ovention C2000, PerfectFry PFA570 (stand-in for PFA500), Traulsen G22010, Traulsen G10011, STS R Rack.
  **Not there** (get from KCL, or build from Edgar's DWG blocks): Traulsen UST3212-L, Traulsen UHT48-LR, Metro
  TWS3030SU-304B-K, AEQ fab tables 60x36 / 72x30 / 84x30, Quantum shelving (WR74-1836, WR74-1848, WR63-1860, 2472),
  Advance Tabco WS-12-108, T&S B-0133 and B-0230-02.
- Store folders (each has Edgar's DWG and the DXF we exported): GA also has Edgar's issued
  `263 GA FAYETTEVILLE TINSELTOWN-QF101-R1.pdf` (**the drafting standard to match**, image in
  `docs/redlines/GA-263_QF101-R1_edgar_reference.png`), the heat-load report PDF and the spec PDF.
- `PH MASTER EQUIPMENT LIST - W PRICING.xlsx` (Edgar's AutoQuotes master with prices: INTERNAL),
  `CINEMARK_PH_MASTER.pdf` (AQ estimate + cut sheets: the source for verified utilities),
  `PH MASTER EQUIPMENT LIST - DRAFT.xlsx` (older draft), SSG proposal PDFs.

**Output location (Edgar, 2026-10-05): all drawings and estimates go to Drive, in each store's own folder.**
Suggested names: `<STORE FOLDER NAME>-QF-SET-R<n>.pdf`, `...-3D-R<n>.png`, the quote PDF
(`26_<ST>_CINEMARK_<slug>_PH-CONSTRUCTION_SSG-2026-CNK-PH01-<nnn>-C1_R<n>_<mm-dd-yy>.pdf`) and its `_INTERNAL.xlsx`.
The internal workbook is for AEQ only; it may live in the store folder (internal shared drive) but never goes to the
customer. Ask Edgar if he wants a subfolder (e.g. `DRAWINGS\`, `ESTIMATE\`).

DWG -> DXF (headless AutoCAD), script file contents:
```
FILEDIA 0
_.DXFOUT "C:\AEQ\work\TX-093\src\TX 093 MCALLEN HOLLYWOOD.dxf" _V 2018 16
_.QUIT _Y
```
Run `accoreconsole.exe /i "<copy of the dwg in C:\AEQ\work>" /s "<script.scr>"`, wait on the process
(`[Diagnostics.Process]::Start(...).WaitForExit()`; `Start-Process -Wait` hangs), then copy the DXF to the store folder.
DXFs are 80-125 MB.

---

## 3b. Status after the redo (2026-10-05, Claude)

Done and checked sheet by sheet (images), GA-263 and TX-093 rebuilt, saved to Drive (`DRAWINGS\`, `ESTIMATE\`):
- Walls from the DWG lines exactly: collinear pieces joined, face pairs split with remainders kept, single lines
  built as room faces off the equipment side, exact-thickness wall types (`AEQ - Wall 5-1/2"`). TX back wall back.
- Rotation = the DWG's (plus the AQ/KCL block front convention); the wall-normal rule is gone.
- Families made from Edgar's DWG blocks where there is no KCL family (KCL 3D mesh blocks -> real geometry with
  coplanar faces merged; 2D AQ blocks -> his plan linework + a body). KCL families aligned body-to-body, back to the
  wall. Fab tables 2.2 / 2.4 from blocks too (the TFSS/FSS stand-in locks its manufacturer).
- Hand sink at 35" A.F.F. to the bowl's front rim; wall shelf provisional 60"; countertop units on the base's top.
- Native tags: AEQ_TAG_EQUIPMENT (+ _BY_OTHERS double bubble for PH / X) from the template's Door Tag, utility key
  tag (serif) on colour-coded connection symbols (AEQ_CONN_*), QF002 tag format (E4.1, H1.1, C1.2, D1.3).
- Rough-in groups (connections within 2 ft on one wall) at 8" O.C., dimensioned from the nearest wall face, the
  rest located in QF103 remarks. Plans 1/4"; kitchens too big split into area sheets (TX: QF101A / QF101B ...);
  north arrow + graphic scale; per-wall interior elevations (QF403 / QF404); schedules one row per item.
Open: NJ-187 (room copy), wall shelf height, DWG gaps (TX X5 bins ~4" and PH3 ~1.4" off the wall in the DWG),
stand-in family sizes (Atosa / PerfectFry / ACP), KCL families for the items built from blocks if Edgar prefers.

## 4. What Edgar rejected on 2026-10-05, and why it happened

His words: "I hate the way the tags are set up - the arrow on the leader is way too big, and the numbers don't seem to
be connected to the leader - it looks like you are using a pdf print instead of the native revit function. Missing
walls, crooked equipment. That is not crooked in the original dwg that I gave you - EQUIPMENT IS IN THE MIDDLE OF
WALLS! These drawings are not even good enough to call a rough draft. The hand sink is in the floor. You didn't use the
characters I provided to the table under the c2000 or the prep cooler next to it. You are a senior architect and a
seasoned drafter - redo this entire presentation. All drawings and estimates need to be saved to the drive under their
respective location folder." Then: "If you don't have the block it can be retrieved from kcl."

His redline of TX-093 QF101 is `docs/redlines/TX-093_QF101_redline_2026-10-05.png`:
- **WALL MISSING** along the back of the left room (behind item 5 dishwasher, X1 3-comp sink, X4 tables).
- The second **X4 table drawn crooked** (circled). It is straight in the DWG.
- A **"?"** at the short wall stubs / jog next to that area (lines that may not be walls, or walls built wrong).
- Red lines + arrows on **X5 ice bins, PH3 / X7 reach-ins (below the middle wall) and the units above the middle wall**:
  their backs belong at the wall face; the units currently sit into / across the walls.
- **Tags 4 and 6** (top right) and **PH2** (right) circled: leaders pointing at the wrong unit or crossing over.

### 4.1 Tags (all sheets)
- Cause: every tag is a free **TextNote** with a leader (text type "AEQ - Utility Tags - 3/32 inch", Times New Roman,
  leader arrowhead "Arrow 30 Degree" 1/8"), placed by script. That is why it looks like a PDF markup: text not bound to
  the element, oversized arrows, leaders that don't meet the text cleanly.
- The template has **no equipment, generic-model or multi-category tag families** (only door/room/wall/window/keynote
  tags). Revit 2027 here has only tag *templates* (`C:\ProgramData\Autodesk\RVT 2027\Family Templates\English-Imperial\Annotations\Multi-Category Tag.rft`,
  `Generic Tag.rft`, `Electrical Equipment Tag.rft`), no library tag families.
- Fix: native **IndependentTag**s on the elements.
  - Equipment item tag: Specialty Equipment (or Multi-Category) tag reading **Mark** (item no.). Shape per Edgar's
    standard (QF101-R1 and its symbols legend): a small rounded / stadium bubble with the item number, thin leader, **no
    arrowhead** (or a 1/32"-1/16" dot) ending on the equipment. "By others" items get the double-outline variant.
  - Utility key tags (E# electrical, P# plumbing; legend "UTILITY KEY TAG (E / P / G / R)"): circle-with-line tag on the
    utility markers (Generic Models carrying the AEQ Utility parameters), reading the tag number parameter.
  - The Revit API cannot create a Label in a tag family. Workable routes: (a) ask Edgar for AEQ's tag families;
    (b) open a template family that already has a Mark label (Door Tag / Window Tag) with `doc.EditFamily`, change
    `OwnerFamily.FamilyCategory` to Specialty Equipment Tags / Multi-Category Tags, redraw the outline, save as
    `AEQ_TAG_EQUIPMENT.rfa` and load it; (c) build it once by hand in the Revit UI on this machine and add it to
    `CAD TEMPLATES` (best long-term: then also add it to the template .rte).
  - Leaders: straight, square to the wall where possible, tags in tidy rows outside the equipment line, angled only for
    close-set or stacked units (Edgar's rule). Never let a leader cross text or another leader. Tags must point at the
    right unit (4 / 6 / PH2 were wrong).

### 4.2 Crooked equipment / equipment in the middle of walls
- Cause 1: `dxf_extract.wall_behind()` replaces the drawn rotation with one computed from the nearest wall's normal
  (`revit_rotation`). It picked a diagonal wall for TX X4 (drawn 0 deg, built 303 deg), nudged TX PH4 UHT48 to 175.9 deg
  and 2.1 to 177.1 deg, and flipped a TX X6 (0 -> 270) and several GA items. **Remove this.** Use the DWG block's
  insertion point and rotation exactly.
- Cause 2: placement aligns the family's bounding-box centre to the block's bounding-box centre. Family bboxes include
  door swings, clearances, connectors and service space, and the KCL family's origin / front axis differs from the AQ
  block's, so units shift into walls and turn backwards. Fix: per family, calibrate the family origin + front direction
  against the block's base point + front (KCL families: front is usually -Y in the family; AQ blocks often draw front
  to +X). Place by insertion point + rotation + that per-family offset. Then **verify**: overlay every placed family's
  plan outline on the DWG block outline; any difference over ~1/2" fails the build (write a check that reports it).
- The equipment's back must sit at the wall face. Edgar marked this on X5, PH3, X7 and the units above TX's middle wall.

### 4.3 Missing / wrong walls
- `dxf_extract` builds walls only from pairs of parallel lines 2"-14" apart on wall layers (store `layer_map`: GA
  `Layer1` -> A-WALL, TX / NJ `0` -> A-WALL) inside the store's `plan_window`. Single lines, polyline walls with width,
  walls inside blocks / xrefs, arcs, or faces further apart are dropped; stray parallel lines (counters, casework) can
  become fake walls (the "?" area).
- Fix: audit every wall in each DWG against the model (overlay the DWG linework on the Revit plan, or link the DWG as
  an underlay and compare). Walls must match the DWG exactly in position, thickness and extent. Check door openings.
  Wall types: the template has interior types (e.g. Interior 4 7/8"); AQ face pairs are 4" (reported as a type
  mismatch). Pick or create a type that matches the drawn thickness.

### 4.4 Hand sink in the floor
- The 7-PS-65 family is level-based; it was placed at the level, so it sits on the floor. Set its elevation / offset
  so the rim is at the manufacturer's mounting height (from the cut sheet in `CINEMARK_PH_MASTER.pdf`; typical rim
  34" AFF, verify). Same check for every wall-hung or countertop item: WS-12-108 wall shelf, faucets, ACP / PerfectFry /
  Ovention on their bases (stacking logic exists: `q["on"]`, catalog `stack` + `height_in`).

### 4.5 "You didn't use the characters I provided" (table under the C2000, prep cooler next to it)
- "Characters" = the blocks in his DWG. Items without a Revit family were drawn as **grey placeholder boxes**
  (DirectShape). That includes GA/TX **2.1 AEQ fab table 60x36** (block `d2c2a85d-4cb0-4bf5-ac7d-a76dbb162f7e`, AQ
  model TFSS-365, under the C2000), **PH5 Traulsen UST3212-L** (block `700ffe7a-309d-4a5c-ac4e-1ee370afd3c4`),
  **PH4 Traulsen UHT48-LR** (block `4e31fa31-f370-4f6c-8ef1-838f077abfde`), GA PH6 Metro TWS3030
  (`c9a20734fd3143dfbf2658fb269e63d10.dwg`), TX X8 Quantum 2472 (`2472GY_R3`), GA X1.1 WS-12-108, X1.2 / X1.3 faucets.
- Fix (in order): KCL Revit family for the exact model (Edgar: "it can be retrieved from KCL"); else build a Specialty
  Equipment family from the DWG block geometry (plan linework from the block + 3D extrusions at the real heights from the
  cut sheet). **No placeholder boxes in a deliverable.**

### 4.6 Overall drafting standard
- Match Edgar's QF101-R1 (`docs/redlines/GA-263_QF101-R1_edgar_reference.png`): title "FOODSERVICE EQUIPMENT PLAN" +
  scale, equipment schedule on the plan sheet (ItemNo, Quantity, Unit, Category, Mfr, Model, Equipment Remarks),
  EQUIPMENT SYMBOLS legend, GENERAL NOTES, north arrow + graphic scale, revision block, title block fields filled.
- 1/4" = 1'-0" default (Edgar). TX printed at 1/8" because the drawing has two areas; better: one view per area or a
  key plan + enlarged 1/4" plans, not a cramped 1/8".
- Line weights and halftone: walls heavier than equipment; existing (X) equipment distinguishable from new; owner (PH)
  items marked "by others" style per the legend.
- Elevations: interior elevations of each equipment wall with rough-in heights; 3D view. Rough-in plans (QF202 plumbing,
  QF302 electrical) with the utility key tags and the QF103 combined utility schedule.
- Dimensions: locate rough-ins from fixed walls; aisle / clearance dimensions where they matter.

---

## 5. Business rules for numbering and scope

- Item numbers come from the **PH master list** (`config/master_items.json`, generated from
  `PH MASTER EQUIPMENT LIST - W PRICING.xlsx`; refresh with `python tools/master_items.py --refresh "<xlsx>"`).
  Same number for the same product on every Pizza Hut project.
- Prefix = who provides it:
  - plain number (1, 2.1, 4, 5, 6): **AEQ supplies and installs**
  - **X** (was E in older drawings): **existing**, relocated by us with **new utilities**
  - **PH**: **provided by owner** (Pizza Hut); **received at AEQ Nashville, then transported to site when ready**;
    we provide utilities and install
- Matching: by model (AQ data / KCL hyperlink), by `match` regex equivalents, or by footprint for fab tables (`size`).
  A model that fits two numbers takes the lower `rank` (7-PS-65: new 1 vs existing X2) and is reported;
  `item_overrides` `{"<block handle>": "X2"}` in the store file settles it.
- Items not on the master list = **existing at that store** (Edgar): next X numbers after the master's (TX: X4 ELAG
  24x72 tables, X5 Follett SG1650 bins, X6 Hoshizaki F1A, X7 Hoshizaki F2A, X8 Quantum 2472, X9 STS rack). They go on
  the schedule as existing.
- Edgar's equipment decisions: keep the Traulsen UHT48; the 60" **Atosa AUF60SD** (AEQ supplies) goes **under the
  Amana/ACP and the PerfectFry**; use the Hoshizaki UF60A family as its Revit stand-in (UR48B blocks in older layouts =
  the Atosa); GA's staged extra blocks were left out on purpose; the mislinked ACP block is an ACP.
- Master list (24 items): 1 Advance 7-PS-65 hand sink; 2.1-2.5 AEQ fab worktables 60x36 (open base), 48x30, 72x30,
  96x30, 84x30 with 5" backsplash; 3.1-3.3 Quantum wire shelving 1836 / 1848 / 1860; 4 ACP MXP22TLT; 5 Hobart LXnR;
  6 Atosa AUF60SD; X1 Advance FC-3-2030-24RL 3-comp sink with X1.1 WS-12-108 shelf, X1.2 T&S B-0133, X1.3 T&S B-0230-02;
  X2 existing 7-PS-65; X3 PerfectFry PFA500; PH1 Ovention C2000-SB; PH2 Traulsen G10011; PH3 Traulsen G22010;
  PH4 Traulsen UHT48-LR; PH5 Traulsen UST3212-L; PH6 Metro TWS3030SU-304B-K.
- Verified utilities (from `CINEMARK_PH_MASTER.pdf`): G10011 115/1 3.8 A 5-15P; G22010 7.6 A; Atosa AUF60SD 115/1 2.6 A
  5-15P; UST3212 7.2 A; UHT48 7.2 A; PerfectFry PFA500 208/1 24 A 5.0 kW 6-30P. Others: see `config/families.json`
  (`verified` flags) and `config/families_synced.json` (data read from the KCL families by the Revit build).

---

## 6. Construction narrative (Edgar, 2026-10-04) = the basis of the estimates

All stores: price **all utilities as new**, run from a panel **about 40 ft away**; most runs go **up, over and back
down**. Ask Edgar if ceiling height needs clarification.

- **GA-263 Fayetteville**: remodel of an old **closed storage room**. Add **5 tons** of cooling as **2 mini-split heads x
  2.5 tons**; system cost to us **$7,200**, marked up appropriately. **Line sets 65 ft**; the roof **penetration already
  exists**; a **crane** is needed for the condensing unit; **new wire** to the condensing unit, new line sets, **new
  drains** for the heads. Demo about **$8,000 including dumpsters**.
- **TX-093 McAllen and NJ-187 Somerset** (nearly identical): demo the existing **satellite concession stand**: the **back
  counter** (standard cabinets) and the **front counter** (standard theater front counter) come out; erect a **wall with
  a cased (bucked) opening on either side** for employees; **standard finish**. Standard cabinets and front counters
  exist in the footprint the new layout goes into. No HVAC.

Where it lives: each store file's `scope`, `hvac`, `finishes`, `demo`, `electrical_service`, `customer_verify`.
Provisional values are flagged in the files.

Draft totals (2026-10-04/05, **not issued**): GA-263 **$165,500**, TX-093 **$160,900**, NJ-187 not priced.

Open questions for Edgar (asked, unanswered):
1. NJ-187: which of the two room copies in the DWG is the design (sets its `plan_window`)?
2. Ceiling heights at TX and NJ (10 ft assumed).
3. Is the GA $8,000 demo a sell price (as priced) or our cost?
4. Crane: $2,500 allowance OK? Markup on the $7,200 system: 30% OK?
5. TX / NJ front counter LF, back counter LF, new wall LF and height (20 LF each, wall to ceiling, provisional).
6. PH equipment freight: our truck from Nashville (TX 1,150 mi each way, about $6,800) or LTL?
7. Does AEQ-supplied equipment go on a separate AEQ equipment quote? (The SSG construction quote lists it as "set &
   connected"; purchase not included.)
8. Finishes: TX / NJ anything beyond the new wall? GA: keep FRP, washable ceiling, quarry tile, cove base ($28,750)?
9. Licensing: AEQ has no TX mechanical license (no HVAC in TX now, so moot unless scope changes); NJ needs licensed
   plumbing / electrical of record.

Estimate format (keep): customer PDF by section (General Conditions, Demolition, Construction & Finishes, Electrical,
Plumbing, HVAC, Equipment Set & Start-Up, Travel & Supervision), lump-sum total incl. travel, scope text that only
describes what is priced, equipment lists split owner-furnished (PH) / supplied by AEQ / existing, schedule (skips
holidays), inclusions, exclusions / assumptions, verify-at-site items, acceptance block. INTERNAL xlsx: lines with
hours, material, sub, cost, sell, margin.

---

## 7. Code map (`C:\AEQ\design\cinemark-pizza-hut`)

| File | Role |
|---|---|
| `CLAUDE.md`, `README.md`, `docs/HANDOFF.md` | standing rules and state; read them |
| `docs/DXF_STANDARD.md` | how layouts are read (layers, AQ data, rough-ins, master numbering) |
| `docs/REVIT_WORKFLOW.md` | Revit steps, first-run checklist |
| `tools/dxf_extract.py` | DXF -> layout.json: walls (face pairing), rooms (derived from walls when no A-AREA polyline), equipment (block match, AQ XRECORD data, KCL hyperlinks), rough-in on the wall behind each item, **`wall_behind` rotation override (remove, see 4.2)**, stacking, master numbering |
| `tools/master_items.py` | master list load / refresh / assign |
| `tools/takeoff.py` | layout -> quantities, circuits, plumbing, heat load |
| `estimating/estimate.py` | takeoff -> priced lines from `config/cost_db.json` + store scope |
| `estimating/build_quote.py`, `quote_doc.py` | customer PDF + INTERNAL xlsx |
| `estimating/schedule.py` | timeline + Gantt (holidays skipped) |
| `tools/run_store.py` | runs the whole pricing chain for one store |
| `revit/AEQ.extension/lib/aeq_cinemark/build.py` | all Revit work: model from template, walls, equipment placement, placeholders, QF plan views, utility markers, tags, elevations, 3D, schedules, sheets, PDF export |
| `revit/batch/build_store.py` | headless entry point for `pyrevit run` |
| `config/families.json` | catalog: models, utilities, `rfa`, `block_aliases`, `stack`, `height_in`, `verified` |
| `config/families_synced.json` | utilities / connectors read from the KCL families |
| `config/stores/*.json` | per-store: addresses, `layer_map`, `plan_window`, `item_numbers: master`, `item_overrides`, narrative scope, travel |
| `tests/` | 15 tests |

Pricing run:
```
python tools/run_store.py GA-263 --dxf "C:\AEQ\work\GA-263\src\263 GA FAYETTEVILLE TINSELTOWN.dxf" --out C:\AEQ\work\GA-263
python tools/run_store.py TX-093 --dxf "C:\AEQ\work\TX-093\src\TX 093 MCALLEN HOLLYWOOD.dxf" --out C:\AEQ\work\TX-093
```

Headless Revit build (what the scratch script did; about 80-110 s per store):
1. Copy `layout.json` and `takeoff.json` from `C:\AEQ\work\<store>` into `C:\AEQ\work\<store>\_AEQ OUTPUT\`; copy the
   DXF next to the store folder.
2. Write `%LOCALAPPDATA%\AEQ\revit_job_active.json` (UTF-8, no BOM):
   `{"store_id": "TX-093", "outputs_dir": "C:\\AEQ\\work\\TX-093\\_AEQ OUTPUT", "work_dir": "C:\\AEQ\\work", "queued": "<iso time>"}`
3. `pyrevit run "C:\AEQ\design\cinemark-pizza-hut\revit\batch\build_store.py" --revit=2027`
4. Result: `<outputs_dir>\REVIT_STATUS.txt`; errors: `%LOCALAPPDATA%\AEQ\revit_build_error.txt`. Model
   `C:\AEQ\work\<store>\<store>_PH.rvt`, set `<DXF name>-QF-SET-R0.pdf`, `...-3D-R0 - 3D View - QF502 - 3D KITCHEN.png`.

Revit 2027 / IronPython gotchas already solved in build.py:
- `ElementId.IntegerValue` is gone: use `.Value`.
- IronPython cannot read `ElementType.Name`: use `Element.Name.__get__(el)` (helper `_name`).
- Enum names: `ZoomFitType`, `ShadowViewsFileType`; `LeaderAtachement` (sic).
- `TextNote.Create` returns None for empty text.
- The template's QF views carry AEQ view templates that own scale and phase: set scale on the view template, do not
  set phase on template views. Phases: "Existing", "New Construction".
- Template QF sheet set: QF001 cover/index, QF002 legend/notes, QF101 equipment plan, QF102 equipment schedule
  (Specialty Equipment), QF103 combined utility schedule (Generic Models with "AEQ Utility *" params), QF111 demo,
  QF201 plumbing, QF202 plumbing rough-in, QF203 ventilation, QF204 refrigeration, QF301 electrical, QF302 electrical
  rough-in, QF401 special conditions, QF402 wall backing; the build adds QF403 interior elevations and QF502 3D.
  Title block `AEQ_TITLEBLOCK_11X17_2026`. Re-creating template viewports with the same type keeps their titles on sheet.
- Template annotation available: text types "AEQ - Notes - 3/32 inch" / "1/8 inch" (Arial), arrowheads incl.
  "Dot Filled 1/16\"", "Arrow Open 90 Degree 1/16\"", "Diagonal 3/64\"" (create a smaller one if needed), dimension
  type "AEQ - Architectural - 3/32 inch", Keynote tags.

AutoQuotes data in the DWG/DXF: root-dictionary XRECORDs `AQSL-AQXBLOCKDATA` / `AQSL-AQXPROJECTDATA` hold raw-DEFLATE
of base64 UTF-16 XML keyed by block handle (decimal): LineItemNumber, Manufacturer, Model, Spec, Width, Depth.
KCL blocks carry `PE_URL` hyperlink xdata "PDF Cutsheet for (Mfr)-Model". Dynamic block true names via
`AcDbBlockRepBTag`. All decoded in `dxf_extract.py`.

---

## 8. Recommended plan for the redo

1. Confirm with Edgar: output folder layout on Drive; NJ room copy; whether he has AEQ tag families; KCL downloads list.
2. Get the missing families (KCL) or build them from his DWG blocks (4.5). No placeholders.
3. Equipment placement from the DWG exactly (4.2): remove the wall-normal rotation; per-family origin / front calibration;
   automatic overlay check (family outline vs block outline, walls vs DWG lines) that fails the build on mismatch.
4. Walls (4.3): rebuild from the DWG and verify against it; fix the missing TX back wall and the "?" stubs.
5. Heights (4.4): mounting heights for wall-hung and countertop items.
6. Annotation (4.1, 4.6): native tags (equipment Mark tag, utility key tag), Edgar's legend / notes / schedule layout,
   dimensions, 1/4" plans (split TX into areas or enlarged plans).
7. Re-run pricing after the layout is right (quantities follow the drawing), regenerate quote PDF + INTERNAL xlsx.
8. Review every sheet as an image yourself before calling anything done; compare to Edgar's QF101-R1 and redline.
9. Save to the Drive store folders (section 3), commit with tests passing, tell Edgar exactly what changed and what is
   still open.

## 9. Acceptance checklist (per store)

- [ ] Every wall in the DWG is in the model, same position and thickness; no fake walls.
- [ ] Every item sits exactly where the DWG has it (insertion, rotation, back at the wall face); overlay check passes.
- [ ] No placeholder boxes; every item is a real family (KCL or built from Edgar's block).
- [ ] Wall-hung / countertop items at their real heights (hand sink rim at mounting height, not on the floor).
- [ ] Tags are native Revit tags, bound to the elements, small clean leaders without oversized arrows, tidy rows,
      pointing at the right units.
- [ ] QF101 laid out like Edgar's QF101-R1 (schedule, symbols legend, general notes, north arrow, graphic scale).
- [ ] Rough-in plans + QF103 schedule agree with the takeoff; elevations show rough-in heights.
- [ ] Quote PDF scope text matches what is priced; no internal numbers; tests pass.
- [ ] All drawings and estimates saved in the store's Drive folder; nothing of Edgar's overwritten.
