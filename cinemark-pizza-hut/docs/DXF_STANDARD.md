# DXF layout standard (survey -> pipeline)

Export from AutoCAD with **SAVEAS > AutoCAD 2018 DXF** into the store's Drive folder, named as in the store
file's `layout_dxf` (e.g. `TX 093 MCALLEN HOLLYWOOD.dxf`). Set `INSUNITS` to inches (1) or feet (2).
The extractor is tolerant: anything it can't classify is reported, never guessed.

## Layers (matched by substring, case-insensitive)

| What | Layer contains | Notes |
|---|---|---|
| Existing walls | `WALL` | draw both faces as LINEs / polylines; faces 2"-14" apart pair into one wall with that thickness |
| Walls to demolish | `WALL` + `DEMO` (e.g. `A-WALL-DEMO`) | priced as partition demo; Revit: existing phase, demolished in New Construction |
| New partitions | `WALL` + `NEW` (e.g. `A-WALL-NEW`) | priced as new partition LF |
| Scope area | `AREA`, `ROOM`, `SCOPE` or `BOUNDARY` (closed polyline) | room SF and perimeter (FRP, base, flooring, ceiling, lighting). Without it the wall extents rectangle is used and a warning is printed. |
| Equipment (new) | any layer without `DEMO`/`EXIST`, e.g. `Q-EQPM` | block inserts |
| Equipment to remove | `DEMO` or `REMOVE`, e.g. `Q-EQPM-DEMO` | counted for removal/disposal and make-safe |
| Existing equipment to remain | `EXIST` | ignored for pricing |
| Doors | block name contains `DOOR`; layer `NEW` = new door | |
| Electrical panel | `E-PANEL` (POINT or block) | home runs measured from here |
| Water / waste source | `P-SOURCE` / `P-WATER`, `P-WASTE` | supply/waste runs measured from here |
| Condenser location | `M-COND` | reserved for line-set length |

A drawing on other layer names (AutoQuotes puts walls on `Layer1`) does not need re-layering: give the
store file a `layer_map` saying which standard layer each one stands for, e.g.
`"layer_map": {"Layer1": "A-WALL"}` (GA-263) or `{"0": "A-WALL"}` (TX-093, NJ-187: walls on layer 0).
A drawing holding more than one copy of the plan gets a `plan_window` (`[x0, y0, x1, y1]`, inches): only
what lies inside it is read (TX-093 reads the lower copy, the one with the equipment).

Item numbers (from AutoQuotes) say who provides each item: plain number = AEQ supplies and installs,
`X` (`E` in older drawings) = existing, relocated with new utilities, `PH` = provided by the owner, received
at AEQ Nashville, utilities and install by AEQ. Blocks AutoQuotes has not linked are identified from their
KCL hyperlink (`PDF Cutsheet for (Traulsen)-G10011`); a block whose AutoQuotes model and hyperlink disagree
is reported.

## AutoQuotes drawings

AutoQuotes stores its project inside the drawing (root-dictionary XRECORDs `AQSL-AQXBLOCKDATA` and
`AQSL-AQXPROJECTDATA`). The extractor reads them, so for every AQ block it has the **AQ item number** (the
number in the drawing's item tags), manufacturer, model, spec text, width and depth, including dynamic
blocks (`*U123`) and KCL blocks AQ has linked. The item number becomes the Revit Mark and the item on
quotes and callouts; the AQ model joins the catalog match. With item numbers on the drawing, the store's
`package` list is checked item by item (missing, extra, quantity, different product) and `run_store.py`
prints an `ITEM` table for review. Identical blocks stacked at one spot are reported (both are counted).

## Rough-in locations

Rough-ins are taken to be on the wall behind the item (about 90% of the time): the item's centre projected
onto the room face of the wall within 18" of its outline that runs along the item's width (from AQ width;
otherwise the nearest wall). Routing lengths and the Revit utility tags use that point, and the same wall
sets the item's orientation in Revit (back to the wall, front into the room). Items with no wall in reach
rough in at the item and keep the rotation drawn.

## Equipment blocks

A block is matched to `config/families.json` in this order:
1. the store file's `block_map` (`{"MY BLOCK NAME": "OVENTION_C2000"}`), for one-off names,
2. the catalog's `block_aliases` regexes against the block name **and** its attribute values,
3. the catalog model number appearing in the block name.

Add an `ITEM` attribute (or put the item number as TEXT within 6 ft of the block) so the Revit Mark,
the rough-in callouts and the quote all carry the same item numbers. KCL / AutoQuotes blocks usually
match on model number with no extra work.
