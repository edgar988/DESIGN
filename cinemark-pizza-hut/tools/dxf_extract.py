#!/usr/bin/env python3
"""DXF layout -> layout.json for the PH / Cinemark retrofit pipeline.

Reads a survey/equipment layout DXF (AutoCAD SAVEAS DXF 2018) and writes a
neutral JSON model used by both the takeoff/estimate (CPython) and the pyRevit
model builder. All geometry in the output is INCHES, plan XY, origin = DXF origin.

Usage:
    python tools/dxf_extract.py <layout.dxf> -o stores/GA-263/layout.json

Conventions are documented in docs/DXF_STANDARD.md. The extractor is tolerant:
anything it cannot classify is listed under "unmatched" / "warnings" instead of
guessed, so the report tells Edgar what to fix in the drawing or catalog.
"""
import argparse
import json
import math
import os
import re
import sys

import ezdxf
from ezdxf.math import Vec2

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# $INSUNITS -> inches
UNIT_TO_IN = {0: 1.0, 1: 1.0, 2: 12.0, 4: 1 / 25.4, 5: 1 / 2.54, 6: 39.3701}

DEFAULT_WALL_THK = 4.875          # 3-5/8 stud + 5/8 GWB both sides
PAIR_MIN, PAIR_MAX = 2.0, 14.0    # in, spacing that counts as two faces of one wall
ANGLE_TOL = math.radians(1.0)

STATUS_RULES = [            # first match wins, tested against upper-case layer name
    ("demo", re.compile(r"DEMO|REMOVE|\bRMV\b|-D$")),
    ("new", re.compile(r"NEW|PROP|-N$")),
    ("exist", re.compile(r"EXIST|EXST|-E$")),
]
WALL_LAYER = re.compile(r"WALL|A-WALL|PARTITION")
ROOM_LAYER = re.compile(r"ROOM|AREA|BOUNDARY|SCOPE")
POINT_LAYERS = {
    "panel": re.compile(r"E-PANEL|PANEL|ELEC.*SOURCE"),
    "water": re.compile(r"P-SOURCE|P-DOMW|WATER.*SOURCE|P-WATER"),
    "waste": re.compile(r"P-SANR|WASTE.*SOURCE|P-WASTE"),
    "condenser": re.compile(r"M-COND|CONDENSER|M-HVAC.*OUT"),
}
DOOR_BLOCK = re.compile(r"DOOR|DR[-_ ]?\d", re.I)


def layer_status(layer, default="exist"):
    """Walls default to existing; equipment defaults to new (see DXF_STANDARD.md)."""
    up = layer.upper()
    for status, rx in STATUS_RULES:
        if rx.search(up):
            return status
    return default


def load_catalog(path=None):
    path = path or os.path.join(ROOT, "config", "families.json")
    with open(path, encoding="utf-8") as f:
        items = json.load(f)["items"]
    compiled = []
    for key, it in items.items():
        pats = [re.compile(a, re.I) for a in it.get("block_aliases", [])]
        model = re.sub(r"[^A-Z0-9]", "", it.get("model", "").upper())
        compiled.append((key, pats, model))
    return items, compiled


def match_equipment(block_name, attribs, compiled, block_map):
    """Return catalog key or None. Order: store block_map, regex on block name,
    regex on attribute values, normalized model contained in block name."""
    if block_name in block_map:
        return block_map[block_name]
    hay = [block_name] + [str(v) for v in attribs.values()]
    for key, pats, _ in compiled:
        for p in pats:
            if any(p.search(h) for h in hay):
                return key
    norm = re.sub(r"[^A-Z0-9]", "", block_name.upper())
    for key, _, model in compiled:
        if model and len(model) >= 5 and model in norm:
            return key
    return None


def _segments(entity, scale):
    t = entity.dxftype()
    if t == "LINE":
        yield (Vec2(entity.dxf.start) * scale, Vec2(entity.dxf.end) * scale)
    elif t in ("LWPOLYLINE", "POLYLINE"):
        pts = [Vec2(p[:2]) * scale for p in (entity.get_points("xy") if t == "LWPOLYLINE"
                                             else [v.dxf.location for v in entity.vertices])]
        closed = entity.closed if t == "LWPOLYLINE" else entity.is_closed
        for a, b in zip(pts, pts[1:] + (pts[:1] if closed else [])):
            if (b - a).magnitude > 0.5:
                yield (a, b)


def pair_walls(segs, status):
    """Pair parallel faces into wall centerlines. Returns list of wall dicts."""
    used = [False] * len(segs)
    walls = []
    for i, (a1, b1) in enumerate(segs):
        if used[i]:
            continue
        d1 = b1 - a1
        L1 = d1.magnitude
        if L1 < 1:
            continue
        u = d1.normalize()
        n = Vec2(-u.y, u.x)
        best = None
        for j in range(i + 1, len(segs)):
            if used[j]:
                continue
            a2, b2 = segs[j]
            d2 = b2 - a2
            if d2.magnitude < 1:
                continue
            ang = abs(math.atan2(u.x * d2.y - u.y * d2.x, u.dot(d2)))
            ang = min(ang, math.pi - ang)
            if ang > ANGLE_TOL:
                continue
            off = (a2 - a1).dot(n)
            if not (PAIR_MIN <= abs(off) <= PAIR_MAX):
                continue
            t2a, t2b = sorted([(a2 - a1).dot(u), (b2 - a1).dot(u)])
            lo, hi = max(0.0, t2a), min(L1, t2b)
            if hi - lo < 0.5 * min(L1, t2b - t2a):
                continue
            if best is None or abs(off) < abs(best[1]):
                best = (j, off, lo, hi)
        if best:
            j, off, lo, hi = best
            used[i] = used[j] = True
            mid = n * (off / 2.0)
            walls.append({"start": list(a1 + u * lo + mid), "end": list(a1 + u * hi + mid),
                          "thickness": round(abs(off), 3), "status": status, "paired": True})
    for i, (a, b) in enumerate(segs):
        if not used[i] and (b - a).magnitude >= 12:
            walls.append({"start": list(a), "end": list(b), "thickness": DEFAULT_WALL_THK,
                          "status": status, "paired": False})
    return walls


def polygon_area_perim(pts):
    a = 0.0
    p = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        a += x1 * y2 - x2 * y1
        p += math.hypot(x2 - x1, y2 - y1)
    return abs(a) / 2.0, p


def extract(path, store=None, catalog_path=None):
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    units = doc.header.get("$INSUNITS", 1)
    scale = UNIT_TO_IN.get(units, 1.0)
    items, compiled = load_catalog(catalog_path)
    block_map = (store or {}).get("block_map", {})
    # store layer_map: drawing layer -> standard layer it stands for, e.g. AutoQuotes "Layer1" -> "A-WALL"
    layer_map = {k.upper(): v for k, v in (store or {}).get("layer_map", {}).items()}

    out = {"source": os.path.basename(path), "insunits": units, "scale_to_in": scale,
           "walls": [], "rooms": [], "doors": [], "equipment": [], "points": {},
           "texts": [], "unmatched": {}, "warnings": []}

    wall_segs = {"exist": [], "demo": [], "new": []}

    def walk(entities, xform_layer=None):
        for e in entities:
            layer = e.dxf.layer if e.dxf.layer != "0" or not xform_layer else xform_layer
            std = layer_map.get(layer.upper(), layer)
            up = std.upper()
            t = e.dxftype()
            if t == "INSERT":
                bname = e.dxf.name
                if bname.startswith("*"):          # anonymous (dynamic/xref) block
                    bname = e.block().name if e.block() else bname
                attribs = {a.dxf.tag.upper(): a.dxf.text for a in e.attribs} if e.attribs else {}
                ins = Vec2(e.dxf.insert) * scale
                rot = e.dxf.get("rotation", 0.0)
                status = layer_status(std, default="new")
                hit_point = False
                for pname, rx in POINT_LAYERS.items():
                    if rx.search(up) or rx.search(bname.upper()):
                        out["points"].setdefault(pname, list(ins))
                        hit_point = True
                if hit_point:
                    continue
                if DOOR_BLOCK.search(bname):
                    out["doors"].append({"block": bname, "x": ins.x, "y": ins.y, "rotation": rot,
                                         "status": layer_status(std, default="exist"),
                                         "layer": layer})
                    continue
                key = match_equipment(bname, attribs, compiled, block_map)
                if key is None and (WALL_LAYER.search(up) or "ANNO" in up or "TITLE" in up):
                    continue
                bbox = None
                try:
                    from ezdxf import bbox as _bb
                    ext = _bb.extents(e.virtual_entities())
                    if ext.has_data:
                        bbox = [ext.extmin.x * scale, ext.extmin.y * scale,
                                ext.extmax.x * scale, ext.extmax.y * scale]
                except Exception:
                    pass
                rec = {"handle": e.dxf.handle, "block": bname, "layer": layer, "key": key,
                       "item": attribs.get("ITEM") or attribs.get("ITEMNO") or attribs.get("TAG"),
                       "x": round(ins.x, 3), "y": round(ins.y, 3), "rotation": round(rot, 3),
                       "xscale": e.dxf.get("xscale", 1.0), "status": status,
                       "attribs": attribs, "bbox": bbox}
                if key is None:
                    out["unmatched"].setdefault(bname, 0)
                    out["unmatched"][bname] += 1
                out["equipment"].append(rec)
            elif t in ("LINE", "LWPOLYLINE", "POLYLINE"):
                if ROOM_LAYER.search(up) and t != "LINE" and (
                        (t == "LWPOLYLINE" and e.closed) or (t == "POLYLINE" and e.is_closed)):
                    pts = [list(Vec2(p[:2]) * scale) for p in (
                        e.get_points("xy") if t == "LWPOLYLINE" else [v.dxf.location for v in e.vertices])]
                    area, per = polygon_area_perim(pts)
                    out["rooms"].append({"layer": layer, "polygon": pts,
                                         "area_sf": round(area / 144.0, 1),
                                         "perimeter_lf": round(per / 12.0, 1)})
                elif WALL_LAYER.search(up):
                    wall_segs[layer_status(std)].extend(_segments(e, scale))
            elif t in ("TEXT", "MTEXT"):
                txt = e.plain_text() if t == "MTEXT" else e.dxf.text
                p = Vec2(e.dxf.insert) * scale
                out["texts"].append({"text": txt.strip(), "x": p.x, "y": p.y, "layer": layer})
            elif t == "POINT":
                for pname, rx in POINT_LAYERS.items():
                    if rx.search(up):
                        out["points"].setdefault(pname, list(Vec2(e.dxf.location) * scale))

    walk(msp)

    for status, segs in wall_segs.items():
        out["walls"].extend(pair_walls(segs, status))

    _tag_items_from_text(out)

    if not out["rooms"]:
        allpts = [w["start"] for w in out["walls"] if w["status"] != "demo"] + \
                 [w["end"] for w in out["walls"] if w["status"] != "demo"]
        if allpts:
            xs, ys = [p[0] for p in allpts], [p[1] for p in allpts]
            poly = [[min(xs), min(ys)], [max(xs), min(ys)], [max(xs), max(ys)], [min(xs), max(ys)]]
            area, per = polygon_area_perim(poly)
            out["rooms"].append({"layer": "(derived from wall extents)", "polygon": poly,
                                 "area_sf": round(area / 144.0, 1), "perimeter_lf": round(per / 12.0, 1),
                                 "derived": True})
            out["warnings"].append("No closed room polyline on a ROOM/AREA layer; room area derived from "
                                   "wall extents (rectangle). Draw A-AREA for an accurate SF.")
        else:
            out["warnings"].append("No walls and no room boundary found - check layer names (docs/DXF_STANDARD.md).")

    for pname in ("panel", "water", "waste"):
        if pname not in out["points"]:
            out["warnings"].append("No %s source point (layer %s). Routing lengths fall back to room "
                                   "centroid + store allowance." % (pname, POINT_LAYERS[pname].pattern))
    if out["unmatched"]:
        out["warnings"].append("Unmatched blocks: %s. Add aliases in config/families.json or a "
                               "block_map in the store file." % ", ".join(sorted(out["unmatched"])))
    out["summary"] = {
        "walls": {s: sum(1 for w in out["walls"] if w["status"] == s) for s in ("exist", "demo", "new")},
        "equipment_new": sum(1 for q in out["equipment"] if q["status"] != "demo" and q["key"]),
        "equipment_demo": sum(1 for q in out["equipment"] if q["status"] == "demo"),
        "unmatched": sum(out["unmatched"].values()),
        "room_sf": round(sum(r["area_sf"] for r in out["rooms"]), 1),
    }
    return out


def _tag_items_from_text(out):
    """Assign item numbers from nearby numeric TEXT when blocks carry no ITEM attribute."""
    nums = [t for t in out["texts"] if re.fullmatch(r"\d{1,3}[A-Z]?", t["text"])]
    for q in out["equipment"]:
        if q["item"] or not q["key"]:
            continue
        cx, cy = q["x"], q["y"]
        if q["bbox"]:
            cx, cy = (q["bbox"][0] + q["bbox"][2]) / 2, (q["bbox"][1] + q["bbox"][3]) / 2
        best = min(nums, key=lambda t: math.hypot(t["x"] - cx, t["y"] - cy), default=None)
        if best and math.hypot(best["x"] - cx, best["y"] - cy) < 72:
            q["item"] = best["text"]
            q["item_from_text"] = True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("dxf")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--store", help="config/stores/<id>.json (for block_map)")
    a = ap.parse_args(argv)
    store = json.load(open(a.store, encoding="utf-8")) if a.store else None
    data = extract(a.dxf, store)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    s = data["summary"]
    print("walls exist/demo/new: %(exist)d/%(demo)d/%(new)d" % s["walls"])
    print("equipment new: %d  demo: %d  unmatched: %d  room SF: %.1f" % (
        s["equipment_new"], s["equipment_demo"], s["unmatched"], s["room_sf"]))
    for w in data["warnings"]:
        print("WARNING:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
