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
`"layer_map": {"Layer1": "A-WALL"}` (GA-263).

## Equipment blocks

A block is matched to `config/families.json` in this order:
1. the store file's `block_map` (`{"MY BLOCK NAME": "OVENTION_C2000"}`), for one-off names,
2. the catalog's `block_aliases` regexes against the block name **and** its attribute values,
3. the catalog model number appearing in the block name.

Add an `ITEM` attribute (or put the item number as TEXT within 6 ft of the block) so the Revit Mark,
the rough-in callouts and the quote all carry the same item numbers. KCL / AutoQuotes blocks usually
match on model number with no extra work.
