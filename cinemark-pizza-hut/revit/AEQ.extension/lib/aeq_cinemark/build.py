# -*- coding: utf-8 -*-
"""Revit 2026 model/sheet builder for the PH / Cinemark retrofit (pyRevit, IronPython 2.7).

Every function takes the active Document and does its own Transaction, so each
ribbon button is one undoable step. Geometry from layout.json is inches; Revit
internal units are feet.

Run clean on GA-263 in Revit 2027 / pyRevit 6.5.5 (headless, revit/batch/build_store.py,
2026-10-04). Views and sheets go into the AEQ 11x17 template's own QF sheet set.
"""
import math
import os
import re
import datetime

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (BuiltInCategory, BuiltInParameter, BoundingBoxXYZ, Color, Curve, CurveLoop,
                               DirectShape, DisplayStyle, DWGImportOptions, Element, ElementId,
                               GeometryCreationUtilities, GeometryObject, ElementTransformUtils, ElevationMarker,
                               FamilySymbol, FilteredElementCollector, GroupType, ImageExportOptions, ImageFileType,
                               ImageResolution, ImportPlacement, ImportUnit, Level, Line, OverrideGraphicSettings,
                               PDFExportOptions, Phase, SaveAsOptions, ScheduleFilter, ScheduleFilterType,
                               ScheduleSheetInstance, ScheduleSortGroupField,
                               StorageType, TextNote, TextNoteLeaderTypes, TextNoteOptions, TextNoteType,
                               Transaction, Transform, View, View3D, ViewDrafting, ViewFamily, ViewFamilyType,
                               ViewOrientation3D, ViewPlan, ViewSchedule, ViewSheet, Viewport, Wall, WallFunction,
                               WallKind,
                               WallType, XYZ, ExportRange, FitDirectionType, ZoomFitType, IFamilyLoadOptions,
                               FamilySource, FamilyInstance, LeaderAtachement)
from Autodesk.Revit.DB.Structure import StructuralType
from System.Collections.Generic import List

from aeq_cinemark import config as C

FT = 1.0 / 12.0   # inches -> feet


# ---------------------------------------------------------------- helpers
def _pt(xy, z=0.0):
    return XYZ(xy[0] * FT, xy[1] * FT, z)


def _first(doc, cls):
    return FilteredElementCollector(doc).OfClass(cls).FirstElement()


def _name(el):
    """Element name. ElementType re-declares Name, which IronPython can't read (AttributeError)."""
    return Element.Name.__get__(el)


def _id_int(eid):
    """ElementId -> int. IntegerValue is gone from the 2026+ API; Value is the 64-bit id."""
    v = getattr(eid, "Value", None)
    return int(v if v is not None else eid.IntegerValue)


def _vft(doc, family):
    for v in FilteredElementCollector(doc).OfClass(ViewFamilyType):
        if v.ViewFamily == family:
            return v
    raise Exception("Template has no view family type %s" % family)


def _level(doc):
    lv = sorted(FilteredElementCollector(doc).OfClass(Level), key=lambda l: l.Elevation)
    if not lv:
        raise Exception("Template has no levels")
    return lv[0]


def _phase(doc, wanted):
    phases = list(doc.Phases)
    for p in phases:
        if p.Name.lower() == wanted.lower():
            return p
    return phases[0] if wanted.lower().startswith("exist") else phases[-1]


def _set(el, name_or_bip, value):
    p = el.get_Parameter(name_or_bip) if not isinstance(name_or_bip, str) else el.LookupParameter(name_or_bip)
    if p is None or p.IsReadOnly:
        return False
    if p.StorageType == StorageType.String:
        return p.Set(str(value))
    if p.StorageType == StorageType.Double:
        return p.Set(float(value))
    if p.StorageType == StorageType.Integer:
        return p.Set(int(value))
    if p.StorageType == StorageType.ElementId:
        return p.Set(value)
    return False


def _unique_name(doc, cls, base):
    names = set(v.Name for v in FilteredElementCollector(doc).OfClass(cls))
    if base not in names:
        return base
    i = 2
    while "%s (%d)" % (base, i) in names:
        i += 1
    return "%s (%d)" % (base, i)


class _Tx(object):
    def __init__(self, doc, name):
        self.t = Transaction(doc, name)

    def __enter__(self):
        self.t.Start()
        return self.t

    def __exit__(self, et, ev, tb):
        if et is None:
            self.t.Commit()
        else:
            self.t.RollBack()
        return False


class _LoadOpts(IFamilyLoadOptions):
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        overwriteParameterValues.Value = True
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        source.Value = FamilySource.Family
        overwriteParameterValues.Value = True
        return True


def room_bbox(layout, margin_in=36.0):
    # every room (a drawing can read as several) and every item, so open areas with equipment stay in frame
    pts = [p for r in layout.get("rooms", []) for p in r["polygon"]]
    if pts:
        for q in layout.get("equipment", []):
            b = q.get("bbox")
            if b and q.get("status") != "demo":
                pts += [[b[0], b[1]], [b[2], b[3]]]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    else:
        pts = [w["start"] for w in layout["walls"]] + [w["end"] for w in layout["walls"]]
        if not pts:     # no walls or room on the drawing: frame the equipment instead
            for q in layout.get("equipment", []):
                b = q.get("bbox")
                pts += [[b[0], b[1]], [b[2], b[3]]] if b else [[q["x"], q["y"]]]
        if not pts:
            raise Exception("layout.json has no room, walls or equipment to frame the views")
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs) - margin_in, min(ys) - margin_in, max(xs) + margin_in, max(ys) + margin_in


# ------------------------------------------------------- 1. new store model
def new_store_model(app, s):
    """Create <outputs>/<store>/<store>.rvt from the AEQ 11x17 template and fill Project Information."""
    st = C.store(s)
    if not os.path.isfile(s["template_rte"]):
        raise IOError("Template not found: %s (Settings)" % s["template_rte"])
    doc = app.NewProjectDocument(s["template_rte"])
    prog = C.read_json(os.path.join(s["repo_root"], "config", "program.json"))
    with _Tx(doc, "AEQ: project information"):
        pi = doc.ProjectInformation
        _set(pi, BuiltInParameter.PROJECT_NAME, "Cinemark #%d %s - Pizza Hut Kitchen" % (   # wraps to 2 lines in the AEQ title block
            st["theatre_no"], st["theatre_name"].split(" (")[0]))
        _set(pi, BuiltInParameter.PROJECT_NUMBER, "SSG-2026-CNK-PH01-%03d" % st["theatre_no"])
        _set(pi, BuiltInParameter.PROJECT_ADDRESS, st["address"])
        _set(pi, BuiltInParameter.CLIENT_NAME, prog["customer"]["name"])
        _set(pi, BuiltInParameter.PROJECT_ISSUE_DATE, datetime.date.today().strftime("%m/%d/%y"))
        _set(pi, BuiltInParameter.PROJECT_STATUS, "Tier 2 Rough-In Package %s" % s.get("rev", "R0"))
    path = os.path.join(C.store_out(s), "%s_PH.rvt" % s["store_id"])
    opts = SaveAsOptions()
    opts.OverwriteExistingFile = True
    doc.SaveAs(path, opts)
    doc.Close(False)
    return path


# ----------------------------------------------- 2. underlay + walls by phase
def link_dxf(doc, dxf_path, view=None):
    view = view or _first_plan(doc)
    opts = DWGImportOptions()
    opts.Unit = ImportUnit.Default          # honour $INSUNITS
    opts.Placement = ImportPlacement.Origin
    opts.ThisViewOnly = False
    ref = clr.Reference[ElementId]()
    with _Tx(doc, "AEQ: link survey DXF"):
        doc.Link(dxf_path, opts, view, ref)
    return ref.Value


def _first_plan(doc):
    for v in FilteredElementCollector(doc).OfClass(ViewPlan):
        if not v.IsTemplate and v.ViewType.ToString() == "FloorPlan":
            return v
    raise Exception("Template has no floor plan view")


def _wall_type_for(doc, thk_in):
    """Closest basic wall type by width: an interior partition when one is within 1" (a 4" face pair is a
    stud partition, not 4" brick), else the closest of any type. Ties go to the thicker type."""
    basics = [w for w in FilteredElementCollector(doc).OfClass(WallType) if w.Kind == WallKind.Basic]
    if not basics:
        raise Exception("Template has no basic wall types")
    err = lambda w: (round(abs(w.Width - thk_in * FT) * 12.0, 3), -w.Width)
    interior = [w for w in basics if w.Function == WallFunction.Interior]
    best = min(interior, key=err) if interior else None
    if best is None or err(best)[0] > 1.0:     # thick (masonry) walls: closest of any type
        best = min(basics, key=err)
    return best, abs(best.Width - thk_in * FT) * 12.0


def build_walls(doc, layout, st):
    lvl = _level(doc)
    ex, nc = _phase(doc, "Existing"), _phase(doc, "New Construction")
    h = (st.get("ceiling_ft") or 10.0) + 2.0
    report = {"created": 0, "type_mismatch_in": []}
    with _Tx(doc, "AEQ: walls from layout"):
        for w in layout["walls"]:
            a, b = _pt(w["start"]), _pt(w["end"])
            if a.DistanceTo(b) < 0.5:
                continue
            wt, err = _wall_type_for(doc, w["thickness"])
            if err > 0.5:
                report["type_mismatch_in"].append(round(w["thickness"], 2))
            wall = Wall.Create(doc, Line.CreateBound(a, b), wt.Id, lvl.Id, h, 0.0, False, False)
            if w["status"] == "new":
                _set(wall, BuiltInParameter.PHASE_CREATED, nc.Id)
            else:
                _set(wall, BuiltInParameter.PHASE_CREATED, ex.Id)
                if w["status"] == "demo":
                    _set(wall, BuiltInParameter.PHASE_DEMOLISHED, nc.Id)
            _set(wall, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, "AEQ layout %s" % w["status"])
            report["created"] += 1
    return report


# --------------------------------------------- 3. families + equipment
def _symbol_for(doc, s, rfa, type_name=None):
    path = os.path.join(s["families_dir"], rfa)
    fam_name = os.path.splitext(rfa)[0]
    fam = None
    for f in FilteredElementCollector(doc).OfClass(clr.GetClrType(FamilySymbol)):
        if f.Family.Name == fam_name:
            fam = f.Family
            break
    if fam is None:
        if not os.path.isfile(path):
            raise IOError("Family file missing: %s" % path)
        res = doc.LoadFamily(path, _LoadOpts())
        ok, fam = (res if isinstance(res, tuple) else (res, None))
        if fam is None:
            for f in FilteredElementCollector(doc).OfClass(clr.GetClrType(FamilySymbol)):
                if f.Family.Name == fam_name:
                    fam = f.Family
                    break
    ids = list(fam.GetFamilySymbolIds())
    syms = [doc.GetElement(i) for i in ids]
    sym = next((x for x in syms if type_name and _name(x) == type_name), syms[0])
    if not sym.IsActive:
        sym.Activate()
        doc.Regenerate()
    return sym


def _nearest_wall(doc, p):
    best, bd = None, 1e9
    for w in FilteredElementCollector(doc).OfClass(Wall):
        crv = w.Location.Curve
        d = crv.Distance(XYZ(p.X, p.Y, crv.GetEndPoint(0).Z))
        if d < bd:
            best, bd = w, d
    return best


def _needs_placeholder(it):
    """Floor equipment worth a placeholder box when there is no family (not accessories or wall shelves)."""
    return bool(it.get("stack") or it.get("height_in") or it.get("category") in ("refrigeration", "cooking"))


def _placeholder(doc, q, it, lvl, phase):
    """No family yet: a box at the drawn footprint (catalog height_in, else 34 in.) so the item shows in
    plans, elevations and 3D, with its Mark and catalog key like a placed family."""
    b = [v * FT for v in q["bbox"]]
    z = lvl.Elevation
    pts = [XYZ(b[0], b[1], z), XYZ(b[2], b[1], z), XYZ(b[2], b[3], z), XYZ(b[0], b[3], z)]
    loop = CurveLoop.Create(List[Curve]([Line.CreateBound(pts[i], pts[(i + 1) % 4]) for i in range(4)]))
    solid = GeometryCreationUtilities.CreateExtrusionGeometry(List[CurveLoop]([loop]), XYZ.BasisZ,
                                                              (it.get("height_in") or 34.0) * FT)
    ds = DirectShape.CreateElement(doc, ElementId(BuiltInCategory.OST_SpecialityEquipment))
    ds.ApplicationId, ds.ApplicationDataId = "AEQ", "placeholder"
    ds.SetShape(List[GeometryObject]([solid]))
    _set(ds, BuiltInParameter.ALL_MODEL_MARK, q.get("item") or "")
    _set(ds, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, q["key"])
    _set(ds, BuiltInParameter.PHASE_CREATED, phase.Id)
    return ds


def place_equipment(doc, layout, catalog, s):
    """Each item's family from CAD TEMPLATES at its drawn spot (a placeholder box where there is no family
    yet), then countertop units set on the base under them."""
    items = catalog["items"]
    lvl = _level(doc)
    nc = _phase(doc, "New Construction")
    placed, skipped, made = [], [], {}
    with _Tx(doc, "AEQ: place equipment"):
        old = [d.Id for d in FilteredElementCollector(doc).OfClass(DirectShape)
               if d.ApplicationId == "AEQ" and d.ApplicationDataId == "placeholder"]
        if old:
            doc.Delete(List[ElementId](old))
        for q in layout["equipment"]:
            if q["status"] == "demo" or not q["key"]:
                continue
            it = items.get(q["key"], {})
            if not it.get("rfa"):
                if q.get("bbox") and _needs_placeholder(it):
                    made[q["handle"]] = _placeholder(doc, q, it, lvl, nc)
                    placed.append((q["key"], _id_int(made[q["handle"]].Id)))
                    skipped.append((q["key"], "no family: placeholder box"))
                else:
                    skipped.append((q["key"], "no .rfa in catalog"))
                continue
            try:
                sym = _symbol_for(doc, s, it["rfa"], it.get("type_name"))
            except Exception as ex:
                skipped.append((q["key"], str(ex)))
                continue
            p = _pt([q["x"], q["y"]], lvl.Elevation)
            try:
                inst = doc.Create.NewFamilyInstance(p, sym, lvl, StructuralType.NonStructural)
            except Exception:
                try:
                    host = _nearest_wall(doc, p)
                    inst = doc.Create.NewFamilyInstance(p, sym, host, lvl, StructuralType.NonStructural)
                except Exception as ex:
                    skipped.append((q["key"], "placement failed: %s" % ex))
                    continue
            rot = q.get("revit_rotation", q.get("rotation", 0)) % 360.0   # block rotation, front-corrected
            if rot > 0.01:
                ElementTransformUtils.RotateElement(doc, inst.Id, Line.CreateBound(p, p + XYZ.BasisZ),
                                                    math.radians(rot))
            doc.Regenerate()
            # KCL insertion points rarely match CAD block base points: align plan bbox centres
            if q.get("bbox"):
                bb = inst.get_BoundingBox(None)
                if bb:
                    cx, cy = (bb.Min.X + bb.Max.X) / 2.0, (bb.Min.Y + bb.Max.Y) / 2.0
                    tx = (q["bbox"][0] + q["bbox"][2]) / 2.0 * FT
                    ty = (q["bbox"][1] + q["bbox"][3]) / 2.0 * FT
                    ElementTransformUtils.MoveElement(doc, inst.Id, XYZ(tx - cx, ty - cy, 0))
            _set(inst, BuiltInParameter.ALL_MODEL_MARK, q.get("item") or "")
            _set(inst, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, q["key"])
            _set(inst, BuiltInParameter.PHASE_CREATED, nc.Id)
            placed.append((q["key"], _id_int(inst.Id)))
            made[q["handle"]] = inst
        doc.Regenerate()
        by_handle = dict((q["handle"], q) for q in layout["equipment"])
        for q in layout["equipment"]:          # ACP / PerfectFry / Ovention onto the base under them
            top = made.get(q["handle"])
            if top is None or not q.get("on"):
                continue
            base = made.get(q["on"])
            bb_base = base.get_BoundingBox(None) if base is not None else None
            z = bb_base.Max.Z if bb_base else lvl.Elevation + (
                items.get(by_handle.get(q["on"], {}).get("key"), {}).get("height_in") or 34.0) * FT
            bb = top.get_BoundingBox(None)
            if bb:
                ElementTransformUtils.MoveElement(doc, top.Id, XYZ(0, 0, z - bb.Min.Z))
    return placed, skipped


# ------------------------------------- 0. read KCL data back into the catalog
_PARAM_RX = re.compile(r"volt|phase|amp|kw|watt|load|nema|plug|cord|connect|water|waste|drain|gas|btu|"
                       r"hz|mca|mocp|breaker|circuit", re.I)


def _params(el):
    out = {}
    for p in el.Parameters:
        n = p.Definition.Name
        if not _PARAM_RX.search(n):
            continue
        try:
            v = p.AsValueString() or p.AsString()
        except Exception:
            v = None
        if v not in (None, ""):
            out[n] = v
    return out


def _num(txt):
    m = re.search(r"[-+]?\d*\.?\d+", txt or "")
    return float(m.group()) if m else None


def _parse_elec(params):
    e = {}
    for n, v in params.items():
        ln = n.lower()
        if "volt" in ln and "volts" not in e:
            e["volts"] = _num(v)
        elif "phase" in ln and "phase" not in e:
            e["phase"] = int(_num(v) or 0) or None
        elif re.search(r"\bamp|current|mca|fla", ln) and "amps" not in e:
            e["amps"] = _num(v)
        elif re.search(r"kw|watt|apparent load", ln) and "kw" not in e:
            x = _num(v)
            e["kw"] = x / 1000.0 if x and ("watt" in ln or "VA" in v) and x > 50 else x
        elif ("nema" in ln or "plug" in ln) and "nema" not in e:
            e["nema"] = v
    return e


def sync_family_data(doc, catalog, s):
    """Write config/families_synced.json from placed instances: KCL parameters + connector
    domains, systems, sizes and heights AFF. takeoff.py overlays it on unverified entries."""
    lvl = _level(doc)
    out = {}
    for inst in FilteredElementCollector(doc).OfClass(FamilyInstance):
        c = inst.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        key = c.AsString() if c else None
        if not key or key not in catalog["items"] or key in out:
            continue
        sym = doc.GetElement(inst.GetTypeId())
        params = _params(sym)
        params.update(_params(inst))
        conns = []
        mep = getattr(inst, "MEPModel", None)
        if mep and mep.ConnectorManager:
            for cn in mep.ConnectorManager.Connectors:
                d = {"domain": str(cn.Domain), "z_aff_in": round((cn.Origin.Z - lvl.Elevation) * 12.0, 1)}
                try:
                    d["system"] = str(cn.PipeSystemType)
                    d["size_in"] = round(cn.Radius * 24.0, 3)
                except Exception:
                    pass
                conns.append(d)
        out[key] = {"revit_family": sym.Family.Name, "revit_type": _name(sym), "params": params,
                    "elec": _parse_elec(params), "connectors": conns,
                    "synced_at": datetime.datetime.now().isoformat()[:19]}
    path = os.path.join(s["repo_root"], "config", "families_synced.json")
    prev = C.read_json(path) if os.path.isfile(path) else {}
    prev.update(out)
    C.write_json(path, prev)
    return path, sorted(out)


# ---------------------------------------------------- 5. views + rough-in
# The AEQ 11x17 template carries the QF sheet set, each plan view on its sheet under an AEQ view
# template. The build draws into those views and only creates a view or sheet the template lacks.
# Interior elevations and 3D have no template sheet: QF403 and QF502 are added.
QF_PLANS = [("QF101", "FOODSERVICE EQUIPMENT PLAN"), ("QF111", "DEMOLITION PLAN"), ("QF201", "PLUMBING PLAN"),
            ("QF202", "PLUMBING ROUGH-IN"), ("QF203", "VENTILATION PLAN"), ("QF204", "REFRIGERATION PLAN"),
            ("QF301", "ELECTRICAL PLAN"), ("QF302", "ELECTRICAL ROUGH-IN"), ("QF401", "SPECIAL CONDITIONS"),
            ("QF402", "WALL BACKING PLAN")]
SCHED_SHEET = ("QF102", "EQUIPMENT SCHEDULE")
UTIL_SHEET = ("QF103", "COMBINED UTILITY SCHEDULE")
ELEV_SHEET = ("QF403", "INTERIOR ELEVATIONS")
VIEW3D_SHEET = ("QF502", "3D VIEW")
SCALES = [48, 64, 96, 128, 192]          # 1/4", 3/16", 1/8", 3/32", 1/16" = 1'-0"


def _view_named(doc, cls, prefix):
    for v in FilteredElementCollector(doc).OfClass(cls):
        if not v.IsTemplate and v.Name.startswith(prefix):
            return v
    return None


def _area(s):
    """Drawing area of the sheet in inches (x0, y0, x1, y1): inside the border, left of the title strip."""
    return s.get("sheet_area_in") or [0.5, 0.65, 13.4, 10.5]


def _fit_scale(s, w_in, h_in, key="plan_scale"):
    """Configured scale (1/4") unless w x h model inches would overflow the drawing area; then the
    next standard scale down that fits."""
    x0, y0, x1, y1 = _area(s)
    start = int(s.get(key, 48))
    for sc in [x for x in SCALES if x >= start] or [start]:
        if w_in / sc <= x1 - x0 and h_in / sc <= y1 - y0 - 0.6:
            return sc
    return SCALES[-1]


def _set_scale(doc, v, sc):
    """View scale; the AEQ view templates control it, so set it on the template (this model only)."""
    if v.Scale == sc:
        return
    try:
        v.Scale = sc
    except Exception:
        pass
    if v.Scale != sc and v.ViewTemplateId != ElementId.InvalidElementId:
        doc.GetElement(v.ViewTemplateId).Scale = sc


def _crop(view, x0, y0, x1, y1):
    bb = BoundingBoxXYZ()
    bb.Min = XYZ(x0 * FT, y0 * FT, -10)
    bb.Max = XYZ(x1 * FT, y1 * FT, 10)
    view.CropBoxActive = True
    view.CropBoxVisible = False
    view.CropBox = bb


def _qf_plan(doc, num, title, lvl, phase):
    """The template's QF plan (its phase and graphics are the template's call), else a new one in phase."""
    v = _view_named(doc, ViewPlan, num + " ")
    if v is None:                        # template without the QF set: make the view, make_sheets adds the sheet
        v = ViewPlan.Create(doc, _vft(doc, ViewFamily.FloorPlan).Id, lvl.Id)
        v.Name = _unique_name(doc, ViewPlan, "%s - %s" % (num, title))
        v.get_Parameter(BuiltInParameter.VIEW_PHASE).Set(phase.Id)
    return v


def _halftone_equipment(doc, view):
    ogs = OverrideGraphicSettings()
    ogs.SetHalftone(True)
    for name in ("OST_SpecialityEquipment", "OST_FoodServiceEquipment", "OST_PlumbingFixtures",
                 "OST_GenericModel"):
        bic = getattr(BuiltInCategory, name, None)
        if bic is None:
            continue
        try:
            view.SetCategoryOverrides(ElementId(bic), ogs)
        except Exception:
            pass


def _centre(q):
    b = q.get("bbox")
    return _pt([(b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0] if b else [q["x"], q["y"]])


def _roughin_targets(layout):
    """(key, item) -> [(rough-in point in feet, on a wall?, item centre)] in drawing order. dxf_extract puts
    rough-ins on the wall behind the item, so tags point where the trades rough in, family or not."""
    out = {}
    for q in layout["equipment"]:
        if q["status"] == "demo" or not q["key"]:
            continue
        c = _centre(q)
        p = _pt(q["roughin"]) if q.get("roughin") else c
        out.setdefault((q["key"], str(q.get("item") or "")), []).append((p, bool(q.get("roughin")), c))
    return out


def _side(p, bbox_in, centre=None):
    """Which way a tag at p faces out: from the item's centre toward its wall when p is on that wall,
    else the nearest room edge."""
    if centre is not None:
        dx, dy = p.X - centre.X, p.Y - centre.Y
        if abs(dx) > 0.1 or abs(dy) > 0.1:
            return ("E" if dx > 0 else "W") if abs(dx) > abs(dy) else ("N" if dy > 0 else "S")
    x0, y0, x1, y1 = [v * FT for v in bbox_in]
    d = {"W": p.X - x0, "E": x1 - p.X, "S": p.Y - y0, "N": y1 - p.Y}
    return min(d, key=d.get)


_FLIP = {"N": "S", "S": "N", "E": "W", "W": "E"}


def _tag_side(layout, p, c, room, reach_in=36.0):
    """(side, anchor) for a tag on the rough-in at p of the item centred at c. Normally the row goes just
    outside the item's wall. A wall with equipment right behind it (two lines of equipment back to back,
    TX-093's middle wall) has no room there: the row goes on the item's own side, past its front, and the
    leader runs square back through it."""
    side = _side(p, room, c)
    if abs(p.X - c.X) < 1e-6 and abs(p.Y - c.Y) < 1e-6:
        return side, p
    px, py, cx, cy = p.X * 12.0, p.Y * 12.0, c.X * 12.0, c.Y * 12.0
    own, others = None, []
    for q in layout["equipment"]:
        b = q.get("bbox")
        if q["status"] == "demo" or not b or q["layer"].upper().startswith("FS-ELEC"):
            continue
        if own is None and abs((b[0] + b[2]) / 2.0 - cx) < 0.5 and abs((b[1] + b[3]) / 2.0 - cy) < 0.5:
            own = b
        else:
            others.append(b)
    if own is None:
        return side, p
    band = {"N": (own[0], py + 1, own[2], py + reach_in), "S": (own[0], py - reach_in, own[2], py - 1),
            "E": (px + 1, own[1], px + reach_in, own[3]), "W": (px - reach_in, own[1], px - 1, own[3])}[side]
    # behind = wholly past the wall face (an outline drawn a little through the wall is not behind it)
    beyond = {"N": lambda b: b[1] > py, "S": lambda b: b[3] < py,
              "E": lambda b: b[0] > px, "W": lambda b: b[2] < px}[side]
    if not any(beyond(b) and b[0] < band[2] and b[2] > band[0] and b[1] < band[3] and b[3] > band[1]
               for b in others):
        return side, p
    front = {"N": XYZ(p.X, own[1] / 12.0, 0), "S": XYZ(p.X, own[3] / 12.0, 0),
             "E": XYZ(own[0] / 12.0, p.Y, 0), "W": XYZ(own[2] / 12.0, p.Y, 0)}[side]
    return _FLIP[side], front


def _text_type(doc, want):
    types = list(FilteredElementCollector(doc).OfClass(TextNoteType))
    hit = next((t for t in types if want and want.lower() in _name(t).lower()), None)
    return (hit or types[0]).Id


def _spread(items, gap):
    """1-D label layout. items: [(ideal, lo, hi)] sorted by ideal: where a label's leader anchor wants to
    be and how far the label reaches either side of it. Overlapping labels are pushed apart evenly, only as
    far as needed, so a label leaves its ideal spot only when a neighbour is in the way."""
    pos = [it[0] for it in items]
    for _ in range(500):
        moved = False
        for i in range(len(items) - 1):
            over = (pos[i] + items[i][2] + gap) - (pos[i + 1] - items[i + 1][1])
            if over > 1e-6:
                pos[i] -= over / 2.0
                pos[i + 1] += over / 2.0
                moved = True
        if not moved:
            break
    return pos


def _seg_hits_box(p, q, b):
    """Does the segment p-q (x, y) cross the box b (x0, y0, x1, y1)? (Liang-Barsky)"""
    t0, t1 = 0.0, 1.0
    dx, dy = q[0] - p[0], q[1] - p[1]
    for pp, qq in ((-dx, p[0] - b[0]), (dx, b[2] - p[0]), (-dy, p[1] - b[1]), (dy, b[3] - p[1])):
        if abs(pp) < 1e-12:
            if qq < 0:
                return False
        else:
            t = qq / pp
            if pp < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return False
    return True


def _clear_of(placed, row, side, gap):
    """Where a row of tags goes to clear the tags already placed (another wall's row at a corner).
    row: [(text bbox, move, leader target)]; returns the extra move as an XYZ. The row first steps out,
    away from its wall; if its leaders would then cross a placed tag it slides along the wall instead, as
    little as it can, and those leaders angle."""
    out = {"N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0)}[side]
    along = (abs(out[1]), abs(out[0]))

    def boxes(sx, sy):
        return [(bb.Min.X + d.X + sx, bb.Min.Y + d.Y + sy, bb.Max.X + d.X + sx, bb.Max.Y + d.Y + sy, t)
                for bb, d, t in row]

    def text_hit(bx, b):
        return bx[0] < b[2] + gap and bx[2] > b[0] - gap and bx[1] < b[3] + gap and bx[3] > b[1] - gap

    def lead_hit(bx, b):
        near = {"N": ((bx[0] + bx[2]) / 2.0, bx[1]), "S": ((bx[0] + bx[2]) / 2.0, bx[3]),
                "E": (bx[0], (bx[1] + bx[3]) / 2.0), "W": (bx[2], (bx[1] + bx[3]) / 2.0)}[side]
        return _seg_hits_box((bx[4].X, bx[4].Y), near, b)

    def clean(sx, sy):
        return not any(text_hit(bx, b) or lead_hit(bx, b) for bx in boxes(sx, sy) for b in placed)

    total = 0.0
    for _ in range(20):
        need = 0.0
        for bx in boxes(out[0] * total, out[1] * total):
            for b in placed:
                if text_hit(bx, b):
                    need = max(need, {"N": b[3] + gap - bx[1], "S": bx[3] - (b[1] - gap),
                                      "E": b[2] + gap - bx[0], "W": bx[2] - (b[0] - gap)}[side])
        if need <= 1e-6:
            break
        total += need
    if not placed or clean(out[0] * total, out[1] * total):
        return XYZ(out[0] * total, out[1] * total, 0)
    step = max(max(bb.Max.X - bb.Min.X, bb.Max.Y - bb.Min.Y) for bb, d, t in row) / 4.0
    for k in range(1, 25):
        for sgn in (1, -1):
            sx, sy = along[0] * sgn * k * step, along[1] * sgn * k * step
            if clean(sx, sy):
                return XYZ(sx, sy, 0)
    return XYZ(out[0] * total, out[1] * total, 0)


class _Callouts(object):
    """Tags in one view, one clean row per wall just outside it. Each tag sits square to its target so its
    leader runs straight; only tags that would overlap (stacked or close-set equipment) are spread apart,
    as little as needed, and get an angled leader."""

    def __init__(self, doc, view, tn_type, frame, gap_ft=1.5):
        self.doc, self.view, self.tn, self.frame = doc, view, tn_type, frame    # frame: x0, y0, x1, y1 ft
        self.g = gap_ft                                 # ft from the wall to the row of tags
        p = doc.GetElement(tn_type).get_Parameter(BuiltInParameter.TEXT_SIZE)
        self.lh = 1.7 * (p.AsDouble() if p else 3.0 / 32.0 / 12.0) * view.Scale   # a line of text, model ft
        self.queue = []

    def add(self, target, text, side, anchor=None):
        """side: which way the tag's wall faces out; anchor: the point on that wall its row is measured
        from (default the target itself, e.g. a wall rough-in)."""
        self.queue.append((target, text, side, anchor or target))

    def place(self):
        """Create the queued tags; returns the extent of tags and leader targets (ft) or None. Each tag is
        measured once created (text outline, and where its leader attaches, which depends on the text
        type), each wall's row is spread from those real sizes, then each tag is moved into place."""
        doc, made = self.doc, []
        for target, text, side, anchor in self.queue:
            n = TextNote.Create(doc, self.view.Id, XYZ(target.X, target.Y, 0), text, TextNoteOptions(self.tn))
            n.LeaderLeftAttachment = LeaderAtachement.Midpoint      # E/W leaders leave the middle of the tag
            n.LeaderRightAttachment = LeaderAtachement.Midpoint
            made.append([n, target, side, anchor])
        if not made:
            return None
        doc.Regenerate()
        for m in made:
            bb = m[0].get_BoundingBox(self.view)             # text only: no leader yet
            lt = TextNoteLeaderTypes.TNLT_STRAIGHT_R if m[2] == "W" else TextNoteLeaderTypes.TNLT_STRAIGHT_L
            m[0].AddLeader(lt).End = XYZ(m[1].X, m[1].Y, 0)
            m.append(bb)
        doc.Regenerate()
        boxes, wanted = [], []
        for side in ("N", "S", "E", "W"):
            ax = side in ("N", "S")                         # tags run along X (N/S walls) or Y (E/W)
            perp = (lambda pt: pt.Y) if ax else (lambda pt: pt.X)
            rows = []
            for m in sorted([m for m in made if m[2] == side], key=lambda m: perp(m[3])):
                if rows and abs(perp(m[3]) - perp(rows[-1][-1][3])) <= 2.0:   # same wall: same row
                    rows[-1].append(m)
                else:
                    rows.append([m])
            for row_ms in rows:
                row_ms.sort(key=lambda m: m[1].X if ax else m[1].Y)
                items, anchors = [], []
                for n, target, _, _, bb in row_ms:
                    # where the leader meets the tag: measured along X; mid-height for E/W (Midpoint)
                    a = list(n.GetLeaders())[0].Anchor if ax else XYZ(0, (bb.Min.Y + bb.Max.Y) / 2.0, 0)
                    anchors.append(a)
                    lo, hi = (a.X - bb.Min.X, bb.Max.X - a.X) if ax else (a.Y - bb.Min.Y, bb.Max.Y - a.Y)
                    items.append((target.X if ax else target.Y, lo, hi))
                h = max(m[4].Max.Y - m[4].Min.Y for m in row_ms)
                pos = _spread(items, 0.6 * h)
                walls = [perp(m[3]) for m in row_ms]
                row = {"N": max(walls) + self.g, "S": min(walls) - self.g,
                       "E": max(walls) + self.g, "W": min(walls) - self.g}[side]
                moves = []
                for (n, target, _, _, bb), a, p in zip(row_ms, anchors, pos):
                    # along the wall: anchor to its spot; across: the text's near edge on the row
                    across = {"N": row - bb.Min.Y, "S": row - bb.Max.Y, "E": row - bb.Min.X,
                              "W": row - bb.Max.X}[side]
                    moves.append(XYZ(p - a.X, across, 0) if ax else XYZ(across, p - a.Y, 0))
                # a row that lands on tags already placed (another wall's row at a corner) steps out, whole
                shift = _clear_of(boxes, [(m[4], d, m[1]) for m, d in zip(row_ms, moves)], side, 0.3 * h)
                for (n, target, _, _, bb), d, p in zip(row_ms, moves, pos):
                    d = d + shift
                    n.Coord = n.Coord + d
                    boxes.append((bb.Min.X + d.X, bb.Min.Y + d.Y, bb.Max.X + d.X, bb.Max.Y + d.Y))
                    if ax:
                        wanted.append((n, p + shift.X))
        doc.Regenerate()
        for n, p in wanted:                 # the attach point can shift as a tag moves: square it up once more
            r = p - list(n.GetLeaders())[0].Anchor.X
            if abs(r) > 1e-4:
                n.Coord = n.Coord + XYZ(r, 0, 0)
        doc.Regenerate()
        for m in made:
            for ld in m[0].GetLeaders():
                ld.End = XYZ(m[1].X, m[1].Y, 0)
        pts = boxes + [(m[1].X, m[1].Y, m[1].X, m[1].Y) for m in made]
        return (min(b[0] for b in pts) - self.lh, min(b[1] for b in pts) - self.lh,
                max(b[2] for b in pts) + self.lh, max(b[3] for b in pts) + self.lh)


def _grow_crop(view, bbox_in, ext_ft, pad_ft=1.0):
    """Crop = the room frame, grown to hold the callouts (Revit sizes a viewport by its annotations too)."""
    if ext_ft is None:
        return
    x0, y0, x1, y1 = bbox_in
    _crop(view, min(x0, (ext_ft[0] - pad_ft) * 12.0), min(y0, (ext_ft[1] - pad_ft) * 12.0),
          max(x1, (ext_ft[2] + pad_ft) * 12.0), max(y1, (ext_ft[3] + pad_ft) * 12.0))


_PIPE_SYS = {"DomesticHotWater": "HW", "DomesticColdWater": "CW", "Sanitary": "SAN", "Vent": "VENT",
             "OtherPipe": "PIPE", "FireProtectOther": "FP"}


def _inch_frac(x):
    """1.5 -> 1-1/2, 0.75 -> 3/4 (nearest 1/8)."""
    if x is None:
        return "?"
    whole, eighths = divmod(int(round(float(x) * 8)), 8)
    frac = {0: "", 1: "1/8", 2: "1/4", 3: "3/8", 4: "1/2", 5: "5/8", 6: "3/4", 7: "7/8"}[eighths]
    return "%d-%s" % (whole, frac) if whole and frac else (frac or str(whole))


_SVC_DESC = {"CW": "COLD WATER", "HW": "HOT WATER", "SAN": "WASTE", "VENT": "VENT"}
_CAT_SVC = [("hw", "HW"), ("cw", "CW"), ("waste", "SAN")]
UTIL = "AEQ Utility"                    # the template's QF103 parameters: "AEQ Utility TAG", ...


def _receptacle(nema):
    m = re.search(r"(L?\d{1,2}-\d{2})", nema or "")
    return "RECEPTACLE %sR" % m.group(1) if m else "RECEPTACLE, NEMA VERIFY"


def utility_rows(layout, takeoff, s):
    """One row per rough-in, numbered E1.. / P1..: what the QF103 Combined Utility Schedule lists and the
    plan tags point to. Electrical: one per circuit. Plumbing: the family's pipe connectors, plus any
    catalog service the family lacks (standard height, VERIFY). Points are the wall rough-ins."""
    where = _roughin_targets(layout)
    items = C.catalog(s)["items"]
    rd = C.read_json(os.path.join(s["repo_root"], "config", "program.json"))["rough_in_defaults"]
    synced = _synced(s)
    sched = {(r["key"], str(r.get("item") or "")): r for r in takeoff["schedule"]}

    def label(key, item):
        r = sched.get((key, item)) or {}
        return ("%s %s" % (r.get("mfr", ""), r.get("model", ""))).strip() or key

    rows, seen = [], {}
    for c in takeoff["circuits"]:
        k = (c["key"], str(c.get("item") or ""))
        pts = where.get(k) or []
        i = seen.get(k, 0)
        seen[k] = i + 1
        if not pts:
            continue
        p, wall, ctr = pts[min(i, len(pts) - 1)]
        flags = [f for f, bad in (("AMPS", c["amps"] is None), ("BKR", not c["breaker_a"]),
                                  ("NEMA", c["conn"] != "direct" and not re.search(r"\d-\d", c["nema"] or ""))) if bad]
        rows.append({"svc": "E", "point": p, "wall": wall, "centre": ctr, "item": k[1], "aff": '%d"' % c["height_aff"],
                     "size": "%s-P %sA" % (c["poles"], c["breaker_a"] or "?"),
                     "desc": "J-BOX, DIRECT CONNECT" if c["conn"] == "direct" else _receptacle(c["nema"]),
                     "to": label(*k), "kw": (items.get(c["key"], {}).get("elec") or {}).get("kw"),
                     "amps": c["amps"], "v": c["volts"], "ph": c["phase"] or 1,
                     "remarks": "CKT %s%s" % (c["circuit"], "; VERIFY " + ", ".join(flags) if flags else "")})
    for (key, item), pts in sorted(where.items()):
        r = sched.get((key, item))
        if not r or not r.get("plumb"):
            continue
        svcs = [(_PIPE_SYS.get(c.get("system", ""), c.get("system") or "PIPE"), _inch_frac(c.get("size_in")) + '"',
                 c["z_aff_in"], "VERIFY HEIGHT" if c["z_aff_in"] < 0 else "")
                for c in synced.get(key, {}).get("connectors", []) if "Piping" in c["domain"]]
        plumb = items.get(key, {}).get("plumb") or {}
        for ck, svc in _CAT_SVC:
            if plumb.get(ck) and svc not in [x[0] for x in svcs]:
                size = plumb[ck] if plumb[ck] == "VERIFY" else plumb[ck] + '"'
                z = rd["cw_hw_supply_sink"] if svc in ("CW", "HW") else rd["waste_wall_sink"]
                svcs.append((svc, size, z, "STD HEIGHT - VERIFY"))
        for p, wall, ctr in pts:
            for svc, size, z, note in svcs:
                desc = _SVC_DESC.get(svc, svc) + (", INDIRECT" if svc == "SAN" and plumb.get("indirect") else "")
                rows.append({"svc": svc, "point": p, "wall": wall, "centre": ctr, "item": item, "aff": '%s"' % z, "size": size,
                             "desc": desc, "to": label(key, item), "kw": None, "amps": None, "v": None, "ph": None,
                             "remarks": note})
    n = {"E": 0, "P": 0}
    for r in rows:
        g = "E" if r["svc"] == "E" else "P"
        n[g] += 1
        r["tag"] = "%s%d" % (g, n[g])
    return rows


def _cube(c, a):
    h = a / 2.0
    pts = [XYZ(c.X - h, c.Y - h, c.Z - h), XYZ(c.X + h, c.Y - h, c.Z - h), XYZ(c.X + h, c.Y + h, c.Z - h),
           XYZ(c.X - h, c.Y + h, c.Z - h)]
    loop = CurveLoop.Create(List[Curve]([Line.CreateBound(pts[i], pts[(i + 1) % 4]) for i in range(4)]))
    return GeometryCreationUtilities.CreateExtrusionGeometry(List[CurveLoop]([loop]), XYZ.BasisZ, a)


def _set_text(el, name, value):
    """Set a parameter from display text (a length or number parameter parses it in project units)."""
    p = el.LookupParameter(name)
    if p is None or p.IsReadOnly or value in (None, ""):
        return
    if p.StorageType == StorageType.String:
        p.Set(str(value))
    else:
        try:
            p.SetValueString(str(value))
        except Exception:
            pass


def _utility_markers(doc):
    return [d for d in FilteredElementCollector(doc).OfClass(DirectShape)
            if d.LookupParameter(UTIL + " TAG") is not None and d.LookupParameter(UTIL + " TAG").AsString()]


def utility_markers(doc, rows, lvl, phase):
    """A small Generic Model at each rough-in (wall point, at its A.F.F.) carrying the AEQ Utility
    parameters, so the template's QF103 Combined Utility Schedule lists it and elevations show where it
    is. Replaces markers from an earlier run. False when the template has no AEQ Utility parameters."""
    old = _utility_markers(doc)
    if old:
        doc.Delete(List[ElementId]([d.Id for d in old]))
    for r in rows:
        z = lvl.Elevation + max(_num(r["aff"]) or 0.0, 0.0) * FT
        ds = DirectShape.CreateElement(doc, ElementId(BuiltInCategory.OST_GenericModel))
        ds.SetShape(List[GeometryObject]([_cube(XYZ(r["point"].X, r["point"].Y, z), 1.5 * FT)]))
        if ds.LookupParameter(UTIL + " TAG") is None:
            doc.Delete(ds.Id)
            return False
        for field, value in (("ITEM", r["item"]), ("TAG", r["tag"]), ("SVC", r["svc"]), ("SIZE", r["size"]),
                             ("DESCRIPTION", r["desc"]), ("LOC.", "WALL" if r["wall"] else "AT EQUIP"),
                             ("A.F.F.", r["aff"]), ("SERVICE TO", r["to"]), ("KW", r["kw"]), ("AMPS", r["amps"]),
                             ("V", r["v"]), ("PH", r["ph"]), ("REMARKS", r["remarks"])):
            _set_text(ds, "%s %s" % (UTIL, field), value)
        _set(ds, BuiltInParameter.PHASE_CREATED, phase.Id)
    return True


_AEQ_TAG = re.compile(r"^[EP]\d+([/-][EP]\d+)*$")


def _tag_text(names):
    """Tags at one rough-in: runs of 3+ become a range ("P7-P11"), others join with "/" ("E1/E2")."""
    runs = []
    for nm in names:
        if runs and nm[0] == runs[-1][-1][0] and int(nm[1:]) == int(runs[-1][-1][1:]) + 1:
            runs[-1].append(nm)
        else:
            runs.append([nm])
    return "/".join("%s-%s" % (r[0], r[-1]) if len(r) > 2 else "/".join(r) for r in runs)


_ITEM_TAG = re.compile(r"^(PH|X|E)?\d+(\.\d+)?$")


def _clear_aeq_notes(doc, view, pattern=_AEQ_TAG):
    """Drop tags from an earlier run (button 5 re-run) so they are not doubled."""
    ids = [n.Id for n in FilteredElementCollector(doc, view.Id).OfClass(TextNote)
           if pattern.match((n.Text or "").strip())]
    if ids:
        doc.Delete(List[ElementId](ids))


def roughin_views(doc, layout, takeoff, s):
    """Frame every QF plan on the room at the fitted scale. Rough-ins become numbered utilities (E1.., P1..):
    a marker per rough-in carrying the QF103 schedule data, and on QF302 / QF202 just the tag numbers,
    leadered to the rough-in on the wall behind each item. Returns the plan views."""
    lvl = _level(doc)
    bbox = room_bbox(layout, 60.0)
    sc = _fit_scale(s, bbox[2] - bbox[0], bbox[3] - bbox[1])
    nc = _phase(doc, "New Construction")
    tn = _text_type(doc, s.get("note_text_type", "AEQ - Utility Tags"))
    views = {}
    with _Tx(doc, "AEQ: QF plans + rough-in"):
        for num, title in QF_PLANS:
            v = _qf_plan(doc, num, title, lvl, nc)
            _crop(v, *bbox)
            _set_scale(doc, v, sc)
            views[num] = v
        v_el, v_pl = views["QF302"], views["QF202"]
        for v in (v_el, v_pl):
            if v.ViewTemplateId == ElementId.InvalidElementId:     # AEQ templates set their own graphics
                _halftone_equipment(doc, v)
            _clear_aeq_notes(doc, v)
        doc.Regenerate()
        rows = utility_rows(layout, takeoff, s)
        utility_markers(doc, rows, lvl, nc)
        room = room_bbox(layout, 0.0)
        frame = [v * FT for v in bbox]
        for view, group in ((v_el, "E"), (v_pl, "P")):
            at = {}                         # one tag note per rough-in point: "P1/P2/P3"
            for r in rows:
                if (r["svc"] == "E") == (group == "E"):
                    key = (round(r["point"].X, 2), round(r["point"].Y, 2))
                    at.setdefault(key, (r["point"], [], r["centre"]))[1].append(r["tag"])
            tags = _Callouts(doc, view, tn, frame, gap_ft=0.75)
            for p, names, ctr in at.values():
                side, anchor = _tag_side(layout, p, ctr, room)
                tags.add(p, _tag_text(names), side, anchor=anchor)
            _grow_crop(view, bbox, tags.place())
        # QF101: item numbers in the same clean rows, leaders to each item's centre
        v_eq = views["QF101"]
        _clear_aeq_notes(doc, v_eq, _ITEM_TAG)
        items = _Callouts(doc, v_eq, tn, frame, gap_ft=1.0)
        for q in layout["equipment"]:
            if q["status"] == "demo" or not q.get("item") or not q.get("bbox") \
                    or q["layer"].upper().startswith("FS-ELEC"):
                continue
            c = _centre(q)
            w = _pt(q["roughin"]) if q.get("roughin") else None
            if w:
                side, anchor = _tag_side(layout, w, c, room)
                items.add(c, str(q["item"]), side, anchor=anchor)
            else:
                items.add(c, str(q["item"]), _side(c, room))
        _grow_crop(v_eq, bbox, items.place())
    return [views[n] for n, _ in QF_PLANS]


def _synced(s):
    p = os.path.join(s["repo_root"], "config", "families_synced.json")
    return C.read_json(p) if os.path.isfile(p) else {}


def _crop_elevation(ev, x0, y0, x1, y1, z0, z1):
    """Crop an elevation to the room's width along the view and floor..ceiling (feet, model coords)."""
    bb = ev.CropBox
    inv = bb.Transform.Inverse
    pts = [inv.OfPoint(XYZ(x, y, z)) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    nb = BoundingBoxXYZ()
    nb.Transform = bb.Transform
    nb.Min = XYZ(min(p.X for p in pts) - 0.5, min(p.Y for p in pts) - 0.5, bb.Min.Z)
    nb.Max = XYZ(max(p.X for p in pts) + 0.5, max(p.Y for p in pts) + 0.5, bb.Max.Z)
    ev.CropBoxActive = True
    ev.CropBoxVisible = False
    ev.CropBox = nb


def interior_elevations(doc, layout, st, s):
    """One elevation marker at the room centre -> four interior elevations, cropped to the room."""
    lvl = _level(doc)
    x0, y0, x1, y1 = room_bbox(layout, 0.0)
    c = XYZ((x0 + x1) / 2.0 * FT, (y0 + y1) / 2.0 * FT, lvl.Elevation)
    h = st.get("ceiling_ft") or 10.0
    plan = _view_named(doc, ViewPlan, "QF101 ") or _first_plan(doc)
    vft = _vft(doc, ViewFamily.Elevation)
    nc = _phase(doc, "New Construction")
    views = []
    with _Tx(doc, "AEQ: interior elevations"):
        mk = ElevationMarker.CreateElevationMarker(doc, vft.Id, c, int(s.get("elev_scale", 48)))
        for i in range(4):
            ev = mk.CreateElevation(doc, plan.Id, i)
            look = ev.ViewDirection.Negate()        # ViewDirection points at the viewer
            if abs(look.Y) >= abs(look.X):
                name, depth = ("NORTH" if look.Y > 0 else "SOUTH"), (y1 - y0) / 2.0 * FT + 0.5
            else:
                name, depth = ("EAST" if look.X > 0 else "WEST"), (x1 - x0) / 2.0 * FT + 0.5
            ev.Name = _unique_name(doc, type(ev), "%s - INTERIOR ELEVATION %s" % (ELEV_SHEET[0], name))
            try:
                ev.get_Parameter(BuiltInParameter.VIEWER_BOUND_OFFSET_FAR).Set(depth)
            except Exception:
                pass
            # new equipment is New Construction: a view left in Existing hides it
            ev.get_Parameter(BuiltInParameter.VIEW_PHASE).Set(nc.Id)
            ev.AreImportCategoriesHidden = True     # the DXF underlay is a line at the floor in elevation
            doc.Regenerate()
            _crop_elevation(ev, x0 * FT, y0 * FT, x1 * FT, y1 * FT, lvl.Elevation, lvl.Elevation + h)
            views.append(ev)
    return views


def view_3d(doc, layout, st):
    lvl = _level(doc)
    x0, y0, x1, y1 = room_bbox(layout, 12.0)
    h = (st.get("ceiling_ft") or 10.0)
    with _Tx(doc, "AEQ: 3D view"):
        v = View3D.CreateIsometric(doc, _vft(doc, ViewFamily.ThreeDimensional).Id)
        v.Name = _unique_name(doc, View3D, "%s - 3D KITCHEN" % VIEW3D_SHEET[0])
        sb = BoundingBoxXYZ()
        sb.Min = XYZ(x0 * FT, y0 * FT, lvl.Elevation - 0.5)
        sb.Max = XYZ(x1 * FT, y1 * FT, lvl.Elevation + h - 1.0)    # cut below ceiling to see in
        v.IsSectionBoxActive = True
        v.SetSectionBox(sb)
        eye = XYZ(x0 * FT - 20, y0 * FT - 20, lvl.Elevation + 25)
        tgt = XYZ((x0 + x1) / 2 * FT, (y0 + y1) / 2 * FT, lvl.Elevation + 2)
        fwd = (tgt - eye).Normalize()
        up = fwd.CrossProduct(XYZ.BasisZ).CrossProduct(fwd).Normalize()
        v.SetOrientation(ViewOrientation3D(eye, up, fwd))
        v.DisplayStyle = DisplayStyle.Realistic
        try:
            v.get_Parameter(BuiltInParameter.VIEW_PHASE).Set(_phase(doc, "New Construction").Id)
        except Exception:
            pass
        try:                                    # see the equipment through the near walls; no level heads
            ogs = OverrideGraphicSettings()
            ogs.SetSurfaceTransparency(50)
            v.SetCategoryOverrides(ElementId(BuiltInCategory.OST_Walls), ogs)
            v.SetCategoryHidden(ElementId(BuiltInCategory.OST_Levels), True)
            v.SetCategoryHidden(ElementId(BuiltInCategory.OST_GenericModel), True)   # utility markers
            v.AreImportCategoriesHidden = True
        except Exception:
            pass
    return v


def equipment_schedule(doc):
    """QF102: the template's AEQ equipment schedule when its category covers all placed equipment;
    otherwise one built here with the same fields (multi-category when equipment spans categories)."""
    cats = {}
    for inst in FilteredElementCollector(doc).OfClass(FamilyInstance):
        c = inst.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if c and c.AsString() and inst.Category and re.match(r"^[A-Z0-9_x]+$", c.AsString()):
            cats[_id_int(inst.Category.Id)] = inst.Category.Id
    if not cats:
        return None
    tpl = _view_named(doc, ViewSchedule, "AEQ - QF102")
    if tpl is not None and list(cats) == [_id_int(tpl.Definition.CategoryId)]:
        return tpl
    want = ([tpl.Definition.GetField(i).GetName() for i in range(tpl.Definition.GetFieldCount())] if tpl
            else ["Mark", "Family and Type", "Count", "Comments"])
    with _Tx(doc, "AEQ: equipment schedule"):
        cat = list(cats.values())[0] if len(cats) == 1 else ElementId.InvalidElementId
        vs = ViewSchedule.CreateSchedule(doc, cat)
        vs.Name = _unique_name(doc, ViewSchedule, "%s - EQUIPMENT SCHEDULE" % SCHED_SHEET[0])
        d = vs.Definition
        fields = {}
        for sf in d.GetSchedulableFields():
            n = sf.GetName(doc)
            if n in want and n not in fields:
                fields[n] = d.AddField(sf)
        if "Mark" in fields:
            d.AddSortGroupField(ScheduleSortGroupField(fields["Mark"].FieldId))
        if len(cats) > 1 and "Comments" in fields:      # multi-category: only the AEQ-placed equipment
            d.AddFilter(ScheduleFilter(fields["Comments"].FieldId, ScheduleFilterType.HasValue))
        d.IsItemized = True
    return vs


ROUGHIN_GROUP = "AEQ ROUGH-IN TABLE"


def roughin_schedule_drafting(doc, takeoff, title="ELECTRICAL ROUGH-IN (FROM TAKEOFF)"):
    """Fallback for templates without the AEQ Utility parameters (no utility markers): a table of circuits
    from takeoff.json (text + detail lines) drawn straight onto the QF103 sheet under the template's
    schedule (a 1:1 viewport there would overwrite the sheet's N.T.S. scale), or into a drafting view when
    there is no QF103. Returns the sheet or view drawn on, or None when the QF103 schedule has the data."""
    cols = [("CKT", 0.45), ("ITEM", 0.45), ("EQUIPMENT", 2.2), ("V", 0.45), ("PH", 0.35), ("AMPS", 0.55),
            ("BKR", 0.65), ("CONN", 0.9), ("NEMA", 0.8), ("AFF", 0.5), ("RUN LF", 0.6)]
    rows = [[str(c["circuit"]), str(c.get("item") or ""), c["key"], str(c["volts"]), str(c["phase"] or 1),
             str(c["amps"] if c["amps"] is not None else "VERIFY"),
             "%s-P %sA" % (c["poles"], c["breaker_a"] or "?"), c["conn"] or "", c["nema"] or "",
             '%d"' % c["height_aff"], str(int(c["home_run_lf"]))] for c in takeoff["circuits"]]
    sh = next((x for x in FilteredElementCollector(doc).OfClass(ViewSheet) if x.SheetNumber == UTIL_SHEET[0]), None)
    with _Tx(doc, "AEQ: rough-in schedule"):
        for gt in FilteredElementCollector(doc).OfClass(GroupType):     # table from an earlier run
            if _name(gt) == ROUGHIN_GROUP:
                doc.Delete(gt.Id)
        if _utility_markers(doc):       # the template's QF103 schedule lists the utility markers itself
            return None
        if sh is not None:
            v, x0, top = sh, 0.55 / 12.0, 10.25 / 12.0
            for si in FilteredElementCollector(doc, sh.Id).OfClass(ScheduleSheetInstance):
                bb = None if si.IsTitleblockRevisionSchedule else si.get_BoundingBox(sh)
                if bb:
                    top = min(top, bb.Min.Y - 0.4 / 12.0)
        else:
            v = ViewDrafting.Create(doc, _vft(doc, ViewFamily.Drafting).Id)
            v.Name = _unique_name(doc, ViewDrafting, "%s - %s" % (UTIL_SHEET[0], title))
            v.Scale = 1
            x0, top = 0.0, 0.0
        tn = _text_type(doc, "AEQ - Notes - 3/32")
        ids = [TextNote.Create(doc, v.Id, XYZ(x0, top, 0), title, TextNoteOptions(tn)).Id]
        rh = 0.022          # ft on sheet at 1:1 (~1/4")
        y0 = y = top - 0.25 / 12.0
        x_edges = [x0]
        for _, w in cols:
            x_edges.append(x_edges[-1] + w / 12.0)
        for r in [[c for c, _ in cols]] + rows:
            for c_i, txt in enumerate(r):
                if txt:                         # Revit makes no note for empty text (returns None)
                    ids.append(TextNote.Create(doc, v.Id, XYZ(x_edges[c_i] + 0.004, y - 0.004, 0), txt,
                                               TextNoteOptions(tn)).Id)
            y -= rh
            ids.append(doc.Create.NewDetailCurve(v, Line.CreateBound(XYZ(x0, y, 0), XYZ(x_edges[-1], y, 0))).Id)
        for x in x_edges:
            ids.append(doc.Create.NewDetailCurve(v, Line.CreateBound(XYZ(x, y0, 0), XYZ(x, y, 0))).Id)
        ids.append(doc.Create.NewDetailCurve(v, Line.CreateBound(XYZ(x0, y0, 0), XYZ(x_edges[-1], y0, 0))).Id)
        try:                                    # grouped so a re-run (button 5) replaces it, not doubles it
            Element.Name.__set__(doc.Create.NewGroup(List[ElementId](ids)).GroupType, ROUGHIN_GROUP)
        except Exception:
            pass
    return v


# -------------------------------------------------------------- 6. sheets
def _titleblock(doc, name):
    """Title block named in Settings, else the one the template's own sheets use (AEQ 11x17)."""
    tbs = list(FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_TitleBlocks)
               .WhereElementIsElementType())
    if not tbs:
        raise Exception("Template has no title block")
    if name:
        for t in tbs:
            if name.lower() in (t.Family.Name + " " + _name(t)).lower():
                return t
    for e in FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_TitleBlocks).WhereElementIsNotElementType():
        return doc.GetElement(e.GetTypeId())
    return tbs[0]


def _size(vp):
    o = vp.GetBoxOutline()
    return o.MaximumPoint.X - o.MinimumPoint.X, o.MaximumPoint.Y - o.MinimumPoint.Y


def _rows(vps, width, gap):
    """Shelf-pack viewports into rows, widest first."""
    rows = []
    for vp in sorted(vps, key=lambda p: -_size(p)[0]):
        w = _size(vp)[0]
        for r in rows:
            if sum(_size(p)[0] for p in r) + gap * len(r) + w <= width:
                r.append(vp)
                break
        else:
            rows.append([vp])
    return rows


def _place(doc, sh, views, s, rescale=True):
    """Viewports for views on sheet sh, packed in rows and centred in the drawing area. rescale: step the
    views down the standard scales until they fit (plans keep the scale roughin_views fitted)."""
    x0, y0, x1, y1 = [a / 12.0 for a in _area(s)]
    gx, gy = 0.5 / 12.0, 0.6 / 12.0               # gy leaves room for the view title under each row
    on_sheet = dict((_id_int(p.ViewId), p) for p in FilteredElementCollector(doc, sh.Id).OfClass(Viewport))
    vps = []
    for v in views:
        old = on_sheet.get(_id_int(v.Id))
        vp_type = None
        if old is not None:                 # template viewport: its title offset fits the old crop, so
            vp_type = old.GetTypeId()       # re-create it (same viewport type) to get the default title
            doc.Delete(old.Id)
        if not Viewport.CanAddViewToSheet(doc, sh.Id, v.Id):
            continue
        vp = Viewport.Create(doc, sh.Id, v.Id, XYZ((x0 + x1) / 2.0, (y0 + y1) / 2.0, 0))
        if vp_type is not None:
            vp.ChangeTypeId(vp_type)
        vps.append(vp)
    if not vps:
        return
    while True:
        doc.Regenerate()
        rows = _rows(vps, x1 - x0, gx)
        widths = [sum(_size(p)[0] for p in r) + gx * (len(r) - 1) for r in rows]
        heights = [max(_size(p)[1] for p in r) for r in rows]
        fits = max(widths) <= x1 - x0 and sum(heights) + gy * len(rows) <= y1 - y0
        cur = max(doc.GetElement(p.ViewId).Scale for p in vps)
        smaller = [x for x in SCALES if x > cur]
        if fits or not rescale or not smaller:
            break
        for p in vps:
            _set_scale(doc, doc.GetElement(p.ViewId), smaller[0])
    y = (y0 + y1) / 2.0 + (sum(heights) + gy * len(rows)) / 2.0
    for r, w, h in zip(rows, widths, heights):
        x = (x0 + x1) / 2.0 - w / 2.0
        for p in r:
            pw, ph = _size(p)
            p.SetBoxCenter(XYZ(x + pw / 2.0, y - h / 2.0, 0))
            x += pw + gx
        y -= h + gy


def make_sheets(doc, s):
    """Lay the build onto the template's QF sheets: re-centre the plans (crop and scale changed), add QF403
    elevations and QF502 3D, the rough-in table under the QF103 schedule, and an equipment schedule built
    here (if any) in place of the template's on QF102. Returns the sheets touched."""
    tb = _titleblock(doc, s.get("titleblock_name"))
    sheets = dict((sh.SheetNumber, sh) for sh in FilteredElementCollector(doc).OfClass(ViewSheet))
    made = []

    ref = sheets.get(QF_PLANS[0][0])            # template sheet: Drawn / Checked By for added sheets

    def sheet(num, name):
        if num not in sheets:
            sh = ViewSheet.Create(doc, tb.Id)
            sh.SheetNumber, sh.Name = num, name
            for bip in (BuiltInParameter.SHEET_DRAWN_BY, BuiltInParameter.SHEET_CHECKED_BY,
                        BuiltInParameter.SHEET_DESIGNED_BY, BuiltInParameter.SHEET_APPROVED_BY):
                p = ref.get_Parameter(bip) if ref else None
                if p and p.AsString():
                    _set(sh, bip, p.AsString())
            sheets[num] = sh
        made.append(sheets[num])
        return sheets[num]

    with _Tx(doc, "AEQ: QF sheets"):
        for num, title in QF_PLANS:
            v = _view_named(doc, ViewPlan, num + " ")
            if v is not None:
                _place(doc, sheet(num, title), [v], s, rescale=False)
        elevs = [v for v in FilteredElementCollector(doc).OfClass(View)
                 if not v.IsTemplate and v.Name.startswith(ELEV_SHEET[0] + " ")]
        if elevs:                               # start at 1/4", _place steps down until they fit
            for v in elevs:
                _set_scale(doc, v, int(s.get("elev_scale", 48)))
            _place(doc, sheet(*ELEV_SHEET), sorted(elevs, key=lambda v: v.Name), s)
        v3 = _view_named(doc, View3D, VIEW3D_SHEET[0] + " ")
        if v3 is not None:
            _set_scale(doc, v3, int(s.get("plan_scale", 48)))
            _place(doc, sheet(*VIEW3D_SHEET), [v3], s)
        es = _view_named(doc, ViewSchedule, SCHED_SHEET[0] + " ")      # built here: template's didn't fit
        if es is not None:
            sh = sheet(*SCHED_SHEET)
            pt = XYZ(0.55 / 12.0, 10.25 / 12.0, 0)
            placed = False
            for si in FilteredElementCollector(doc, sh.Id).OfClass(ScheduleSheetInstance):
                if si.ScheduleId == es.Id:
                    placed = True
                elif doc.GetElement(si.ScheduleId).Name.startswith("AEQ - QF102"):
                    pt = si.Point
                    doc.Delete(si.Id)
            if not placed:
                ScheduleSheetInstance.Create(doc, sh.Id, es.Id, pt)
        rt = _view_named(doc, ViewDrafting, UTIL_SHEET[0] + " ")
        if rt is not None:
            sh = sheet(*UTIL_SHEET)
            top = 10.25 / 12.0
            for si in FilteredElementCollector(doc, sh.Id).OfClass(ScheduleSheetInstance):
                bb = None if si.IsTitleblockRevisionSchedule else si.get_BoundingBox(sh)
                if bb:
                    top = min(top, bb.Min.Y - 0.4 / 12.0)
            vp = next((p for p in FilteredElementCollector(doc, sh.Id).OfClass(Viewport) if p.ViewId == rt.Id), None)
            if vp is None and Viewport.CanAddViewToSheet(doc, sh.Id, rt.Id):
                vp = Viewport.Create(doc, sh.Id, rt.Id, XYZ(0.5, 0.4, 0))
            if vp is not None:
                doc.Regenerate()
                w, h = _size(vp)
                vp.SetBoxCenter(XYZ(0.55 / 12.0 + w / 2.0, top - h / 2.0, 0))
    return made


# ------------------------------------------------------- 7. PDF + 3D render
def export_pdf_and_render(doc, s, st):
    out = C.store_out(s)
    sheets = sorted([sh for sh in FilteredElementCollector(doc).OfClass(ViewSheet)
                     if sh.SheetNumber.startswith("QF")], key=lambda x: x.SheetNumber)
    ids = List[ElementId]([sh.Id for sh in sheets])
    opts = PDFExportOptions()
    opts.Combine = True
    base = "%s-QF-SET-%s" % (os.path.basename(st.get("drive_folder", st["store_id"])), s.get("rev", "R0"))
    opts.FileName = base
    doc.Export(out, ids, opts)
    renders = []
    for v in FilteredElementCollector(doc).OfClass(View3D):
        if v.Name.startswith(VIEW3D_SHEET[0] + " "):
            io = ImageExportOptions()
            io.ExportRange = ExportRange.SetOfViews
            io.SetViewsAndSheets(List[ElementId]([v.Id]))
            io.FilePath = os.path.join(out, base.replace("QF-SET", "3D"))
            io.ZoomType = ZoomFitType.FitToPage
            io.FitDirection = FitDirectionType.Horizontal
            io.PixelSize = 3600
            io.ImageResolution = ImageResolution.DPI_300
            io.ShadowViewsFileType = ImageFileType.PNG      # (sic) the API's name for shaded views
            io.HLRandWFViewsFileType = ImageFileType.PNG
            doc.ExportImage(io)
            # Revit appends " - <view type> - <view name>" to FilePath for a set of views
            stem = os.path.basename(io.FilePath)
            renders += [os.path.join(out, f) for f in os.listdir(out)
                        if f.startswith(stem) and v.Name in f and f.lower().endswith(".png")]
    return os.path.join(out, base + ".pdf"), sorted(set(renders))
