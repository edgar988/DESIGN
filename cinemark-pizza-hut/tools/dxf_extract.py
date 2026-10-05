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
import base64
import json
import math
import os
import re
import sys
import zlib
import xml.etree.ElementTree as ET

import ezdxf
from ezdxf.math import Vec2

try:
    from tools import master_items
except ImportError:                     # run as a script from tools/
    import master_items
from_master = master_items.assign
provided_by = master_items.provided_by

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


# ---- AutoQuotes drawings --------------------------------------------------------------------
# AQ keeps its project inside the drawing: root-dictionary XRECORDs whose binary chunks are raw
# DEFLATE of base64 of an XML dictionary keyed by block handle (decimal). AQXBLOCKDATA is the product
# (manufacturer, model, spec, width, depth: the hover text in AutoCAD); AQXPROJECTDATA carries the AQ
# line item number that the drawing's item tags show.

def _aq_record(doc, name):
    xr = doc.rootdict.get(name)
    if xr is None or xr.dxftype() != "XRECORD":
        return {}
    raw = b"".join(t.value for t in xr.tags if isinstance(t.value, bytes))
    try:
        xml = base64.b64decode(zlib.decompress(raw, -15)).decode("utf-8")
    except (zlib.error, ValueError):
        return {}
    if xml.startswith("<?xml"):                  # declares utf-16; the payload is plain text
        xml = xml[xml.index("?>") + 2:]
    out = {}
    d = ET.fromstring(xml).find("__dictionary")
    for it in (d if d is not None else []):
        val = it.find("value")[0]
        out[it.find("key")[0].text] = {c.tag: c.text if len(c) == 0 else [g.text for g in c] for c in val}
    return out


def _float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def aq_products(doc):
    """Block handle (hex, as the DXF writes it) -> AQ item number, manufacturer, model, spec, width,
    depth. Empty for a drawing that did not come from AutoQuotes."""
    blocks, proj = _aq_record(doc, "AQSL-AQXBLOCKDATA"), _aq_record(doc, "AQSL-AQXPROJECTDATA")
    out = {}
    for h, b in blocks.items():
        p = proj.get(h, {})
        if "true" in (b.get("IsDeleted"), p.get("IsDeleted")):
            continue
        out["%X" % int(h)] = {"item": p.get("LineItemNumber"), "mfr": b.get("Manufacturer"),
                              "model": b.get("Model"), "spec": b.get("Spec"), "width": _float(b.get("Width")),
                              "depth": _float(b.get("Depth")), "accessory": p.get("IsAccessory") == "true"}
    return out


def _front_offset(doc, block_name, prod):
    """Degrees to add to a block's rotation so a Revit family (front toward -Y, like KCL blocks) faces the
    same way. Many AQ blocks are drawn with their width along local Y and the front toward +X: that is
    +90. Decided from AQ's width against the block's unrotated outline; 0 when that can't tell. Only for
    free-standing items: an item against a wall faces away from it (see wall_behind)."""
    w = (prod or {}).get("width")
    if not w:
        return 0.0
    try:
        from ezdxf import bbox as _bb
        ext = _bb.extents(doc.blocks[block_name])
    except Exception:
        return 0.0
    if not ext.has_data:
        return 0.0
    dx, dy = abs(ext.size.x - w), abs(ext.size.y - w)
    if dy <= 2.0 < dx:
        return 90.0
    return 0.0


def _hyperlink_product(e):
    """KCL blocks carry an AutoCAD hyperlink (their hover text): 'PDF Cutsheet for (Traulsen)-G10011'."""
    if not e.has_xdata("PE_URL"):
        return None
    txt = [v for c, v in e.get_xdata("PE_URL") if c == 1000]
    m = re.search(r"\((.+?)\)-(.+)$", txt[1] if len(txt) > 1 else "")
    return {"mfr": m.group(1).strip(), "model": m.group(2).strip()} if m else None


def _norm(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def _true_name(doc, name):
    """*U123 (an anonymous reference to a dynamic block) -> the dynamic block's own name."""
    br = doc.block_records.get(name)
    if br is not None and br.has_xdata("AcDbBlockRepBTag"):
        for code, val in br.get_xdata("AcDbBlockRepBTag"):
            if code == 1005 and val in doc.entitydb:
                return doc.entitydb[val].dxf.name
    return name


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


ROUGHIN_REACH = 18.0    # in: an item whose outline is within this of a wall face roughs in on that wall


def _pt_rect(p, b):
    return math.hypot(max(b[0] - p.x, 0.0, p.x - b[2]), max(b[1] - p.y, 0.0, p.y - b[3]))


def _pt_seg(p, a, e):
    d = e - a
    t = max(0.0, min(1.0, (p - a).dot(d) / d.dot(d))) if d.dot(d) else 0.0
    return (p - (a + d * t)).magnitude


def wall_behind(q, walls):
    """The wall an item backs onto, as (rough-in point, unit normal from the wall into the room), inches.
    Rough-ins are ~90% of the time on that wall (Edgar): the item's centre projected onto its room face.
    The wall behind runs along the item's width (AutoQuotes width says which way that is); without AQ
    data, the nearest wall in reach. None when no wall is within reach (island / floor items)."""
    b = q.get("bbox") or [q["x"], q["y"], q["x"], q["y"]]
    c = Vec2((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)
    along = None
    aq = q.get("aq") or {}
    if aq.get("width"):
        # the outline includes door swings, which only add depth: the side that matches AQ's width is it
        dx, dy = abs(b[2] - b[0] - aq["width"]), abs(b[3] - b[1] - aq["width"])
        if abs(dx - dy) >= 1:
            along = Vec2(1, 0) if dx < dy else Vec2(0, 1)
    corners = [Vec2(b[0], b[1]), Vec2(b[2], b[1]), Vec2(b[2], b[3]), Vec2(b[0], b[3])]
    best = None
    for w in walls:
        a, e = Vec2(w["start"]), Vec2(w["end"])
        if (e - a).magnitude < 1:
            continue
        gap = min([_pt_rect(a, b), _pt_rect(e, b)] + [_pt_seg(k, a, e) for k in corners]) - w["thickness"] / 2.0
        if gap > ROUGHIN_REACH:
            continue
        u = (e - a).normalize()
        score = (along is not None and abs(u.dot(along)) < 0.9, _pt_seg(c, a, e))
        if best is None or score < best[0]:
            best = (score, a, e, w["thickness"])
    if best is None:
        return None
    _, a, e, thk = best
    u = (e - a).normalize()
    foot = a + u * max(0.0, min((e - a).magnitude, (c - a).dot(u)))
    n = c - foot
    if n.magnitude < 1e-6:
        return None
    n = n.normalize()
    p = foot + n * (thk / 2.0)
    return [round(p.x, 2), round(p.y, 2)], [round(n.x, 4), round(n.y, 4)]


def stack(out, items):
    """Countertop units (catalog stack 'top': ACP, PerfectFry, Ovention) sit on the base under them in plan
    (stack 'base': undercounter freezer, worktable): q['on'] = that base's handle, the one overlapping the
    most, when it covers at least half the unit's footprint."""
    def area(b):
        return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])

    def overlap(a, b):
        return area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])

    eq = [q for q in out["equipment"] if q["status"] != "demo" and q["key"] and q.get("bbox")]
    bases = [q for q in eq if items.get(q["key"], {}).get("stack") == "base"]
    for q in eq:
        if items.get(q["key"], {}).get("stack") == "top" and bases:
            b = max(bases, key=lambda b: overlap(q["bbox"], b["bbox"]))
            if overlap(q["bbox"], b["bbox"]) >= 0.5 * area(q["bbox"]):
                q["on"] = b["handle"]


def _label(q):
    p = q.get("aq") or q.get("kcl") or {}
    return ("%s %s" % (p.get("mfr") or "", p.get("model") or "")).strip() or q["block"]


def _item_order(n):
    m = re.match(r"\d+", str(n))
    return (int(m.group()) if m else 10 ** 6, str(n))


def item_table(out):
    """One row per item number on the drawing (for review): label, catalog key, count, provided by."""
    rows = {}
    for q in out["equipment"]:
        if q["status"] == "demo" or not q.get("item"):
            continue
        r = rows.setdefault(str(q["item"]), {"item": str(q["item"]), "label": _label(q), "key": q["key"], "count": 0,
                                             "provided_by": q.get("provided_by")})
        r["count"] += 1
    return [rows[n] for n in sorted(rows, key=_item_order)]


def check_package(out, store):
    """Drawing vs the store's package list by item number: missing and extra items, quantities, and a
    different product under the same number. Only when the drawing carries item numbers."""
    pkg = (store or {}).get("package")
    table = out["items"]
    if not isinstance(pkg, list) or not table:
        return
    drawn = dict((r["item"], r) for r in table)
    for p in pkg:
        n = str(p["item"])
        r = drawn.get(n)
        if r is None:
            out["warnings"].append("Package item %s %s is not on the drawing." % (n, p["key"]))
            continue
        if r["key"] != p["key"]:
            out["warnings"].append("Item %s: drawing has %s (%s), package says %s."
                                   % (n, r["label"], r["key"] or "no catalog match", p["key"]))
        if r["count"] != p.get("qty", 1):
            out["warnings"].append("Item %s %s: drawn %d, package qty %d." % (n, p["key"], r["count"], p.get("qty", 1)))
    for n in sorted(set(drawn) - set(str(p["item"]) for p in pkg), key=_item_order):
        out["warnings"].append("Drawing item %s %s is not in the package." % (n, drawn[n]["label"]))


def polygon_area_perim(pts):
    a = 0.0
    p = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        a += x1 * y2 - x2 * y1
        p += math.hypot(x2 - x1, y2 - y1)
    return abs(a) / 2.0, p


ROOM_GAP_IN = 60.0      # openings up to 5 ft (doors, cased openings) close a room; wider fronts stay open
ROOM_CELL_IN = 2.0


def rooms_from_walls(walls, points, gap=ROOM_GAP_IN, cell=ROOM_CELL_IN):
    """The enclosed spaces holding equipment, for drawings with no room polyline: the walls are rasterised,
    openings narrower than `gap` closed, the free space split into spaces, and the spaces that hold any of
    `points` (equipment centres) grown back out to the wall faces. Area and perimeter to the wall faces;
    polygon is the space's bounding rectangle (framing, routing fallback)."""
    import numpy as np
    from collections import deque
    if not walls:
        return []
    xs = [c for w in walls for c in (w["start"][0], w["end"][0])]
    ys = [c for w in walls for c in (w["start"][1], w["end"][1])]
    x0, y0 = min(xs) - gap, min(ys) - gap
    gx = np.arange(x0, max(xs) + gap, cell) + cell / 2.0
    gy = np.arange(y0, max(ys) + gap, cell) + cell / 2.0
    X, Y = np.meshgrid(gx, gy)
    d = np.full(X.shape, np.inf)                # distance to the nearest wall face
    for w in walls:
        (ax, ay), (bx, by) = w["start"], w["end"]
        vx, vy = bx - ax, by - ay
        t = np.clip(((X - ax) * vx + (Y - ay) * vy) / ((vx * vx + vy * vy) or 1e-9), 0, 1)
        d = np.minimum(d, np.hypot(X - (ax + t * vx), Y - (ay + t * vy)) - max(w["thickness"], 2.0) / 2.0)
    wall, r = d <= 0, gap / 2.0
    free = d > r
    H, W = free.shape
    lab = np.zeros(free.shape, int)
    n = 0
    for sy, sx in zip(*np.nonzero(free)):
        if lab[sy, sx]:
            continue
        n += 1
        lab[sy, sx] = n
        dq = deque([(sy, sx)])
        while dq:
            cy, cx = dq.popleft()
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < H and 0 <= nx < W and free[ny, nx] and not lab[ny, nx]:
                    lab[ny, nx] = n
                    dq.append((ny, nx))
    outside = set(lab[0, :]) | set(lab[-1, :]) | set(lab[:, 0]) | set(lab[:, -1])
    k = int(r / cell) + 1
    hit = []
    for px, py in points:
        iy, ix = int((py - y0) // cell), int((px - x0) // cell)
        if not (0 <= iy < H and 0 <= ix < W):
            continue
        # an item against a wall sits in the closed margin: take the space next to it
        win = lab[max(0, iy - k):iy + k + 1, max(0, ix - k):ix + k + 1]
        v = lab[iy, ix] or next((v for v in np.unique(win) if v), 0)
        if v and v not in outside and v not in hit:
            hit.append(v)
    rooms = []
    for v in sorted(hit):
        m = lab == v
        for _ in range(int(round(r / cell))):  # grow back out to the walls (square steps keep the corners)
            g = m.copy()
            g[1:, :] |= m[:-1, :]
            g[:-1, :] |= m[1:, :]
            g[:, 1:] |= g[:, :-1].copy()
            g[:, :-1] |= g[:, 1:].copy()
            m = g & ~wall
        edges = (np.count_nonzero(m[1:, :] != m[:-1, :]) + np.count_nonzero(m[:, 1:] != m[:, :-1])
                 + m[0, :].sum() + m[-1, :].sum() + m[:, 0].sum() + m[:, -1].sum())
        bx0, by0, bx1, by1 = (float(X[m].min() - cell / 2), float(Y[m].min() - cell / 2),
                              float(X[m].max() + cell / 2), float(Y[m].max() + cell / 2))
        rooms.append({"layer": "(derived from walls)", "derived": True,
                      "polygon": [[bx0, by0], [bx1, by0], [bx1, by1], [bx0, by1]],
                      "area_sf": round(float(m.sum()) * cell * cell / 144.0, 1),
                      "perimeter_lf": round(float(edges) * cell / 12.0, 1)})
    return rooms


def extract(path, store=None, catalog_path=None):
    doc = ezdxf.readfile(path)
    msp = doc.modelspace()
    units = doc.header.get("$INSUNITS", 1)
    scale = UNIT_TO_IN.get(units, 1.0)
    items, compiled = load_catalog(catalog_path)
    aq = aq_products(doc)
    block_map = (store or {}).get("block_map", {})
    # store layer_map: drawing layer -> standard layer it stands for, e.g. AutoQuotes "Layer1" -> "A-WALL"
    layer_map = {k.upper(): v for k, v in (store or {}).get("layer_map", {}).items()}
    win = (store or {}).get("plan_window")      # [x0, y0, x1, y1] in: read only this part of the drawing

    def inside(*pts):
        return win is None or all(win[0] <= p[0] <= win[2] and win[1] <= p[1] <= win[3] for p in pts)

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
                if bname.startswith("*"):          # anonymous (dynamic) block
                    bname = _true_name(doc, bname)
                attribs = {a.dxf.tag.upper(): a.dxf.text for a in e.attribs} if e.attribs else {}
                prod = aq.get(e.dxf.handle)
                link = _hyperlink_product(e)
                ins = Vec2(e.dxf.insert) * scale
                if not inside(ins):
                    continue
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
                # the AQ model, else the KCL hyperlink's, joins the match (not the manufacturer: brand
                # aliases like HOBART would catch every product of that brand)
                model = (prod or {}).get("model") or (link or {}).get("model")
                hay = dict(attribs, PRODUCT_MODEL=model) if model else attribs
                key = match_equipment(bname, hay, compiled, block_map)
                if key is None and (WALL_LAYER.search(layer.upper()) or "ANNO" in up or "TITLE" in up):
                    continue
                if prod and link and prod.get("model") and _norm(link["model"]) not in _norm(prod["model"]) \
                        and _norm(prod["model"]) not in _norm(link["model"]):
                    out["warnings"].append("Block %s at (%.0f, %.0f): AutoQuotes says %s, its hyperlink says %s."
                                           % (bname, ins.x, ins.y, prod["model"], link["model"]))
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
                       "item": ((prod or {}).get("item") or attribs.get("ITEM") or attribs.get("ITEMNO")
                                or attribs.get("TAG")),
                       "x": round(ins.x, 3), "y": round(ins.y, 3), "rotation": round(rot, 3),
                       "xscale": e.dxf.get("xscale", 1.0), "status": status,
                       "attribs": attribs, "bbox": bbox}
                if prod:
                    rec["aq"] = prod
                elif link:
                    rec["kcl"] = link
                rec["provided_by"] = provided_by(rec["item"])
                rec["revit_rotation"] = round(rot + _front_offset(doc, e.dxf.name, prod), 3)
                if key is None:
                    out["unmatched"].setdefault(bname, 0)
                    out["unmatched"][bname] += 1
                out["equipment"].append(rec)
            elif t in ("LINE", "LWPOLYLINE", "POLYLINE"):
                if ROOM_LAYER.search(up) and t != "LINE" and (
                        (t == "LWPOLYLINE" and e.closed) or (t == "POLYLINE" and e.is_closed)):
                    pts = [list(Vec2(p[:2]) * scale) for p in (
                        e.get_points("xy") if t == "LWPOLYLINE" else [v.dxf.location for v in e.vertices])]
                    if inside(*pts):
                        area, per = polygon_area_perim(pts)
                        out["rooms"].append({"layer": layer, "polygon": pts,
                                             "area_sf": round(area / 144.0, 1),
                                             "perimeter_lf": round(per / 12.0, 1)})
                elif WALL_LAYER.search(up):
                    wall_segs[layer_status(std)].extend(s for s in _segments(e, scale) if inside(*s))
            elif t in ("TEXT", "MTEXT"):
                txt = e.plain_text() if t == "MTEXT" else e.dxf.text
                p = Vec2(e.dxf.insert) * scale
                if inside(p):
                    out["texts"].append({"text": txt.strip(), "x": p.x, "y": p.y, "layer": layer})
            elif t == "POINT":
                p = Vec2(e.dxf.location) * scale
                for pname, rx in POINT_LAYERS.items():
                    if rx.search(up) and inside(p):
                        out["points"].setdefault(pname, list(p))

    walk(msp)

    for status, segs in wall_segs.items():
        out["walls"].extend(pair_walls(segs, status))

    _tag_items_from_text(out)
    if (store or {}).get("item_numbers") == "master":     # PH program: numbers from the master list
        master = master_items.load_master()
        if master:
            from_master(out, store, master)
    stack(out, items)

    live = [w for w in out["walls"] if w["status"] != "demo"]
    for q in out["equipment"]:
        if q["key"] and q["status"] != "demo":
            hit = wall_behind(q, live)
            q["roughin"] = hit[0] if hit else None
            if hit:     # back to its wall, front into the room: how the drawing has it, whatever the block's axes
                q["revit_rotation"] = round((math.degrees(math.atan2(hit[1][1], hit[1][0])) + 90.0) % 360.0, 3)
    out["items"] = item_table(out)
    check_package(out, store)
    stacked = {}
    for q in out["equipment"]:
        stacked.setdefault((q["block"], round(q["x"], 1), round(q["y"], 1), q["rotation"]), []).append(q)
    for qs in stacked.values():
        if len(qs) > 1:
            out["warnings"].append("%d identical %s blocks at (%.0f, %.0f)%s: duplicate in the drawing? Both are counted."
                                   % (len(qs), _label(qs[0]), qs[0]["x"], qs[0]["y"],
                                      " (item %s)" % qs[0]["item"] if qs[0].get("item") else ""))

    if not out["rooms"]:
        centres = [((q["bbox"][0] + q["bbox"][2]) / 2.0, (q["bbox"][1] + q["bbox"][3]) / 2.0) if q.get("bbox")
                   else (q["x"], q["y"]) for q in out["equipment"]
                   if q["key"] and q["status"] != "demo" and not q["layer"].upper().startswith("FS-ELEC")]
        out["rooms"] = rooms_from_walls(live, centres)
        if out["rooms"]:
            out["warnings"].append("No closed room polyline on a ROOM/AREA layer; room area taken from the walls: "
                                   "%s SF in %d enclosed space(s) holding equipment (openings up to %d in. closed). "
                                   "Draw A-AREA for an exact SF." % (
                                       format(round(sum(r["area_sf"] for r in out["rooms"])), ","),
                                       len(out["rooms"]), ROOM_GAP_IN))
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
        "roughin_on_wall": sum(1 for q in out["equipment"] if q.get("roughin")),
        "autoquotes": bool(aq),
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
