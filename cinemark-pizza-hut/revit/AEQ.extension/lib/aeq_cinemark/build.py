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
                               FamilySource, FamilyInstance, LeaderAtachement, CompoundStructure,
                               MaterialFunctionAssignment, Options, ViewDetailLevel, GeometryInstance, Solid,
                               IndependentTag, Reference, TagOrientation, LeaderEndCondition)
from Autodesk.Revit.DB.Structure import StructuralType
from System.Collections.Generic import List

from aeq_cinemark import config as C
from aeq_cinemark import families as F

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


def _eid(v):
    """int -> ElementId (IronPython can't choose among ElementId's constructors for a plain int)."""
    from System import Int64
    return ElementId(Int64(v))


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
    """A basic wall type of exactly the drawn thickness ("AEQ - Wall 5-1/2\""), made once per thickness from
    the template's interior partition (its core material, one layer), so every wall sits exactly on the
    faces Edgar drew. Returns (type, mismatch in inches) - mismatch is 0 unless the type can't be made."""
    want = round(thk_in * 8) / 8.0
    name = 'AEQ - Wall %s"' % _inch_frac(want)
    basics = [w for w in FilteredElementCollector(doc).OfClass(WallType) if w.Kind == WallKind.Basic]
    for w in basics:
        if _name(w) == name:
            return w, abs(w.Width * 12.0 - thk_in)
    if not basics:
        raise Exception("Template has no basic wall types")
    interior = [w for w in basics if w.Function == WallFunction.Interior] or basics
    src = min(interior, key=lambda w: abs(w.Width * 12.0 - thk_in))
    try:
        nt = src.Duplicate(name)
        cs = nt.GetCompoundStructure()
        mat = ElementId.InvalidElementId
        if cs is not None:
            core = [l for l in cs.GetLayers() if l.Function == MaterialFunctionAssignment.Structure] or list(cs.GetLayers())
            if core:
                mat = core[0].MaterialId
        nt.SetCompoundStructure(CompoundStructure.CreateSingleLayerCompoundStructure(
            MaterialFunctionAssignment.Structure, thk_in * FT, mat))
        return nt, 0.0
    except Exception:
        return src, abs(src.Width * 12.0 - thk_in)


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


REMARK = {"OWNER": "BY OWNER (PH): RECEIVED AT AEQ NASHVILLE, SET & CONNECTED BY AEQ",
          "EXISTING": "EXISTING: RELOCATED, NEW UTILITIES",
          "AEQ": "SUPPLIED & INSTALLED BY AEQ"}
_NOT_BODY = re.compile(r"clear|service|swing|access|zone|hidden|invisible|envelope", re.I)


def _key_of(doc, inst):
    """The catalog key an AEQ-placed instance stands for (its type's Type Comments; one type per key)."""
    t = doc.GetElement(inst.GetTypeId()) if inst is not None else None
    p = t.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_COMMENTS) if t is not None else None
    return p.AsString() if p is not None else None


def _aeq_type(doc, sym, key, it):
    """One type per catalog key carrying the catalog's manufacturer, model and description, so QF102 reads
    right also where a KCL family stands in for another product (Hoshizaki UF60A for the Atosa AUF60SD,
    PFA570 for the PFA500) or one table family serves two fab tables."""
    name = "AEQ - %s" % key
    t = None
    for i in sym.Family.GetFamilySymbolIds():
        x = doc.GetElement(i)
        if _name(x) == name:
            t = x
            break
    if t is None:
        t = sym.Duplicate(name)
    for bip, val in ((BuiltInParameter.ALL_MODEL_MANUFACTURER, it.get("mfr")),
                     (BuiltInParameter.ALL_MODEL_MODEL, it.get("model")),
                     (BuiltInParameter.ALL_MODEL_DESCRIPTION, it.get("description")),
                     (BuiltInParameter.ALL_MODEL_TYPE_COMMENTS, key)):
        _set(t, bip, val or "")
    if not t.IsActive:
        t.Activate()
    return t


def prepare_families(app, doc, layout, catalog, s):
    """Families the build makes (families.py): the AEQ tags, the connection symbols, and an equipment family
    from Edgar's DWG block for every item without a KCL family. Run with no transaction open, before
    place_equipment. Returns ({block name: family name}, notes)."""
    out_dir = os.path.join(s.get("work_dir") or r"C:\AEQ\work", "_families")
    F.tag_families(app, doc, out_dir)
    F.connection_families(app, doc, out_dir)
    items = catalog["items"]
    fams, notes = {}, []
    for q in layout["equipment"]:
        b = q.get("geom_block")
        if q["status"] == "demo" or not q["key"] or b in fams or b not in (layout.get("blocks") or {}):
            continue
        it = items.get(q["key"], {})
        if it.get("rfa"):
            continue
        r = math.radians(q.get("front_offset") or 0.0)       # the item's back in block coordinates
        name, note = F.block_family(app, doc, out_dir, q["key"], b, layout["blocks"][b], it,
                                    (-math.sin(r), math.cos(r)))
        fams[b] = name
        notes.append("%s: %s" % (q["key"], note))
    return fams, notes


def _body_world(q):
    """The drawn block's body outline in plan (inches): its local body box turned and moved by the block's
    insertion. None without block geometry."""
    b = q.get("body_local")
    if not b:
        return None
    r = math.radians(q.get("rotation") or 0.0)
    c, sn = math.cos(r), math.sin(r)
    sx, sy = q.get("xscale") or 1.0, q.get("yscale") or 1.0
    w = [(q["x"] + x * sx * c - y * sy * sn, q["y"] + x * sx * sn + y * sy * c)
         for x in (b[0], b[2]) for y in (b[1], b[3])]
    return [min(p[0] for p in w), min(p[1] for p in w), max(p[0] for p in w), max(p[1] for p in w)]


def _solid_pts(doc, inst):
    """Edge points of the family's solids (feet), leaving out clearance / service / door-swing zones."""
    opt = Options()
    opt.DetailLevel = ViewDetailLevel.Fine
    pts = []

    def walk(geo):
        for go in geo:
            if isinstance(go, GeometryInstance):
                walk(go.GetInstanceGeometry())
            elif isinstance(go, Solid) and go.Faces.Size:
                st = doc.GetElement(go.GraphicsStyleId)
                if st is not None and _NOT_BODY.search(_name(st) or ""):
                    continue
                for e in go.Edges:
                    pts.extend(e.Tessellate())
    geo = inst.get_Geometry(opt)
    if geo is not None:
        walk(geo)
    return pts


def _body_box(doc, inst):
    pts = _solid_pts(doc, inst)
    if not pts:
        bb = inst.get_BoundingBox(None)
        return (bb.Min.X, bb.Min.Y, bb.Min.Z, bb.Max.X, bb.Max.Y, bb.Max.Z) if bb else None
    return (min(p.X for p in pts), min(p.Y for p in pts), min(p.Z for p in pts),
            max(p.X for p in pts), max(p.Y for p in pts), max(p.Z for p in pts))


def _back(q):
    r = math.radians(q.get("revit_rotation") or 0.0)
    return -math.sin(r), math.cos(r)


def _align(doc, inst, q):
    """Move a KCL family so its body matches the drawn block's body: backs flush (the side against the
    wall), centred along the wall. Returns the size difference (width, depth) in inches."""
    tgt = _body_world(q) or q.get("bbox")
    bb = _body_box(doc, inst)
    if not tgt or not bb:
        return None
    tx0, ty0, tx1, ty1 = [v * FT for v in tgt]
    fx0, fy0, _, fx1, fy1, _ = bb
    bx, by = _back(q)
    dx = (tx0 + tx1 - fx0 - fx1) / 2.0
    dy = (ty0 + ty1 - fy0 - fy1) / 2.0
    if abs(bx) > 0.9:
        dx = (tx1 - fx1) if bx > 0 else (tx0 - fx0)
    elif abs(by) > 0.9:
        dy = (ty1 - fy1) if by > 0 else (ty0 - fy0)
    ElementTransformUtils.MoveElement(doc, inst.Id, XYZ(dx, dy, 0))
    return round(abs((fx1 - fx0) - (tx1 - tx0)) * 12.0, 1), round(abs((fy1 - fy0) - (ty1 - ty0)) * 12.0, 1)


def _mount(doc, inst, q, it, lvl):
    """Wall-hung / deck items at their mounting height (catalog 'mount'): the hand sink with the front edge
    of the bowl at 35 in. A.F.F. (Edgar), else the bottom or top of the item at aff_in."""
    m = it.get("mount") or {}
    if not m.get("aff_in"):
        return None
    pts = _solid_pts(doc, inst)
    if not pts:
        return None
    if m.get("ref") == "bowl_front_rim":
        bx, by = _back(q)
        fr = [-(p.X * bx + p.Y * by) for p in pts]          # how far toward the front
        lim = max(fr) - 1.5 * FT
        z = max(p.Z for p, v in zip(pts, fr) if v >= lim)
    elif m.get("ref") == "top":
        z = max(p.Z for p in pts)
    else:
        z = min(p.Z for p in pts)
    ElementTransformUtils.MoveElement(doc, inst.Id, XYZ(0, 0, lvl.Elevation + m["aff_in"] * FT - z))
    return m["aff_in"]


def place_equipment(doc, layout, catalog, s, block_fams=None):
    """Every item where the DWG has it. Items with a family made from Edgar's block go in at the block's
    insertion point and rotation (exact by construction). KCL families go in at the drawn rotation (plus the
    block's front convention), then move so their body matches the block's body, back to the wall; mounted
    items go to their height; countertop units sit on the base under them. Returns (placed, skipped, notes)."""
    items = catalog["items"]
    block_fams = block_fams or {}
    lvl = _level(doc)
    nc = _phase(doc, "New Construction")
    placed, skipped, notes, made = [], [], [], {}
    with _Tx(doc, "AEQ: place equipment"):
        old = [d.Id for d in FilteredElementCollector(doc).OfClass(DirectShape)
               if d.ApplicationId == "AEQ" and d.ApplicationDataId == "placeholder"]
        if old:
            doc.Delete(List[ElementId](old))
        for q in layout["equipment"]:
            if q["status"] == "demo" or not q["key"] or q["layer"].upper().startswith("FS-ELEC"):
                continue
            it = items.get(q["key"], {})
            fam = block_fams.get(q.get("geom_block"))
            try:
                if fam:
                    sym = _aeq_type(doc, F.tag_symbol(doc, fam), q["key"], it)
                    rot = q.get("rotation") or 0.0
                elif it.get("rfa"):
                    sym = _aeq_type(doc, _symbol_for(doc, s, it["rfa"], it.get("type_name")), q["key"], it)
                    rot = q.get("revit_rotation", q.get("rotation", 0))
                else:
                    skipped.append((q["key"], "no family and no block geometry"))
                    continue
            except Exception as ex:
                skipped.append((q["key"], str(ex)))
                continue
            p = _pt([q["x"], q["y"]], lvl.Elevation)
            try:
                inst = doc.Create.NewFamilyInstance(p, sym, lvl, StructuralType.NonStructural)
            except Exception:
                try:
                    inst = doc.Create.NewFamilyInstance(p, sym, _nearest_wall(doc, p), lvl,
                                                        StructuralType.NonStructural)
                except Exception as ex:
                    skipped.append((q["key"], "placement failed: %s" % ex))
                    continue
            rot = rot % 360.0
            if rot > 0.01:
                ElementTransformUtils.RotateElement(doc, inst.Id, Line.CreateBound(p, p + XYZ.BasisZ),
                                                    math.radians(rot))
            doc.Regenerate()
            if not fam:
                diff = _align(doc, inst, q)
                if diff and max(diff) > 1.5:
                    notes.append("%s %s: family body differs from the drawn block by %.1f x %.1f in."
                                 % (q.get("item"), q["key"], diff[0], diff[1]))
                doc.Regenerate()
                if _mount(doc, inst, q, it, lvl):
                    notes.append("%s %s mounted at %s in. A.F.F. (%s)" % (
                        q.get("item"), q["key"], it["mount"]["aff_in"], it["mount"].get("ref", "bottom")))
            _set(inst, BuiltInParameter.ALL_MODEL_MARK, q.get("item") or "")
            _set(inst, BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS, REMARK.get(q.get("provided_by") or "AEQ", ""))
            _set(inst, BuiltInParameter.PHASE_CREATED, nc.Id)
            placed.append((q["key"], _id_int(inst.Id)))
            made[q["handle"]] = inst
            q["revit_id"] = _id_int(inst.Id)
        doc.Regenerate()
        try:                                    # DWG block handle -> Revit element, for buttons 5-7 run later
            C.write_json(os.path.join(C.store_out(s), "revit_ids.json"),
                         dict((h, _id_int(i.Id)) for h, i in made.items()))
        except Exception:
            pass
        by_handle = dict((q["handle"], q) for q in layout["equipment"])
        for q in layout["equipment"]:          # ACP / PerfectFry / Ovention onto the base under them
            top = made.get(q["handle"])
            if top is None or not q.get("on"):
                continue
            base = made.get(q["on"])
            tb = _body_box(doc, top)
            z = None
            if base is not None and tb is not None:
                # the base's top surface under this unit (inset 3": a backsplash or rim at the edge is
                # not what it stands on)
                ins = 3.0 * FT
                zs = [p.Z for p in _solid_pts(doc, base)
                      if tb[0] + ins <= p.X <= tb[3] - ins and tb[1] + ins <= p.Y <= tb[4] - ins]
                z = max(zs) if zs else None
                if z is None:
                    bb_base = base.get_BoundingBox(None)
                    z = bb_base.Max.Z if bb_base else None
            if z is None:
                z = lvl.Elevation + (items.get(by_handle.get(q["on"], {}).get("key"), {}).get("height_in") or 34.0) * FT
            if tb is not None:
                ElementTransformUtils.MoveElement(doc, top.Id, XYZ(0, 0, z - tb[2]))
    return placed, skipped, notes


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
        key = _key_of(doc, inst)
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


def _tag_side(layout, p, c, room, reach_in=36.0, front_box=None):
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
    # the row goes past the item's whole plan outline (door swings included) when the view gave it
    fb = [v * 12.0 for v in front_box] if front_box else own
    front = {"N": XYZ(p.X, fb[1] / 12.0, 0), "S": XYZ(p.X, fb[3] / 12.0, 0),
             "E": XYZ(fb[0] / 12.0, p.Y, 0), "W": XYZ(fb[2] / 12.0, p.Y, 0)}[side]
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


class _TagRows(object):
    """Native Revit tags (IndependentTag) in one view, one clean row per wall just outside it. Each tag sits
    square to its target so its leader runs straight; only tags that would overlap (stacked or close-set
    equipment) are spread apart, as little as needed, and get an angled leader. Rows meeting at a corner
    step out or slide so no tag or leader crosses another tag. Leaders have no arrowhead (AEQ tag types)
    and end on the item / connection symbol."""

    def __init__(self, doc, view, gap_ft=1.0):
        self.doc, self.view, self.g = doc, view, gap_ft
        self.queue = []

    def add(self, element, symbol, target, end, side, anchor=None):
        """target: where the tag wants to sit along its row (item centre / rough-in); end: the leader end
        point; side: which way the row's wall faces out; anchor: the wall point the row is measured from."""
        self.queue.append((element, symbol, target, end, side, anchor or target))

    def place(self):
        doc, view, made = self.doc, self.view, []
        for el, sym, target, end, side, anchor in self.queue:
            try:
                tag = IndependentTag.Create(doc, sym.Id, view.Id, Reference(el), False, TagOrientation.Horizontal,
                                            XYZ(target.X, target.Y, 0))
            except Exception:
                continue
            made.append([tag, target, side, anchor, end])
        if not made:
            return None
        doc.Regenerate()
        for m in made:
            m.append(m[0].get_BoundingBox(view))
        made = [m for m in made if m[5] is not None]
        boxes = []
        for side in ("N", "S", "E", "W"):
            ax = side in ("N", "S")
            perp = (lambda pt: pt.Y) if ax else (lambda pt: pt.X)
            rows = []
            for m in sorted([m for m in made if m[2] == side], key=lambda m: perp(m[3])):
                if rows and abs(perp(m[3]) - perp(rows[-1][-1][3])) <= 2.0:
                    rows[-1].append(m)
                else:
                    rows.append([m])
            for row_ms in rows:
                row_ms.sort(key=lambda m: m[1].X if ax else m[1].Y)
                items = []
                for tag, target, _, _, _, bb in row_ms:
                    hp = tag.TagHeadPosition
                    lo, hi = (hp.X - bb.Min.X, bb.Max.X - hp.X) if ax else (hp.Y - bb.Min.Y, bb.Max.Y - hp.Y)
                    items.append((target.X if ax else target.Y, lo, hi))
                h = max(m[5].Max.Y - m[5].Min.Y for m in row_ms)
                pos = _spread(items, 0.6 * h)
                walls = [perp(m[3]) for m in row_ms]
                row = {"N": max(walls) + self.g, "S": min(walls) - self.g,
                       "E": max(walls) + self.g, "W": min(walls) - self.g}[side]
                moves = []
                for (tag, target, _, _, _, bb), p in zip(row_ms, pos):
                    hp = tag.TagHeadPosition
                    across = {"N": row - bb.Min.Y, "S": row - bb.Max.Y, "E": row - bb.Min.X,
                              "W": row - bb.Max.X}[side]
                    moves.append(XYZ(p - hp.X, across, 0) if ax else XYZ(across, p - hp.Y, 0))
                shift = _clear_of(boxes, [(m[5], d, m[4]) for m, d in zip(row_ms, moves)], side, 0.3 * h)
                for (tag, _, _, _, _, bb), d in zip(row_ms, moves):
                    d = d + shift
                    tag.TagHeadPosition = tag.TagHeadPosition + d
                    boxes.append((bb.Min.X + d.X, bb.Min.Y + d.Y, bb.Max.X + d.X, bb.Max.Y + d.Y))
        doc.Regenerate()
        for tag, _, _, _, end, _ in made:
            try:
                tag.HasLeader = True
                tag.LeaderEndCondition = LeaderEndCondition.Free
                ref = list(tag.GetTaggedReferences())[0]
                tag.SetLeaderEnd(ref, XYZ(end.X, end.Y, 0))
            except Exception:
                pass
        pts = boxes + [(m[4].X, m[4].Y, m[4].X, m[4].Y) for m in made]
        pad = 0.5
        return (min(b[0] for b in pts) - pad, min(b[1] for b in pts) - pad,
                max(b[2] for b in pts) + pad, max(b[3] for b in pts) + pad)


def _grow_crop(view, bbox_in, ext_ft, pad_ft=1.0):
    """Crop = the room frame, grown to hold the callouts (Revit sizes a viewport by its annotations too)."""
    if ext_ft is None:
        return
    x0, y0, x1, y1 = bbox_in
    _crop(view, min(x0, (ext_ft[0] - pad_ft) * 12.0), min(y0, (ext_ft[1] - pad_ft) * 12.0),
          max(x1, (ext_ft[2] + pad_ft) * 12.0), max(y1, (ext_ft[3] + pad_ft) * 12.0))


_PIPE_SVC = {"DomesticHotWater": "H", "DomesticColdWater": "C", "Sanitary": "D"}


def _inch_frac(x):
    """1.5 -> 1-1/2, 0.75 -> 3/4 (nearest 1/8)."""
    if x is None:
        return "?"
    whole, eighths = divmod(int(round(float(x) * 8)), 8)
    frac = {0: "", 1: "1/8", 2: "1/4", 3: "3/8", 4: "1/2", 5: "5/8", 6: "3/4", 7: "7/8"}[eighths]
    return "%d-%s" % (whole, frac) if whole and frac else (frac or str(whole))


# QF002 tag format: service + equipment item + connection suffix (C15.3 = cold water, item 15, connection 3)
_SVC_ORDER = ["E-CORD", "E-DISC", "E-DEVICE", "H", "C", "D", "I", "G"]
_SVC_DESC = {"H": "HOT WATER", "C": "COLD WATER", "D": "DIRECT WASTE", "I": "INDIRECT WASTE TO FLOOR SINK",
             "G": "GAS"}
UTIL = "AEQ Utility"                    # the template's QF103 parameters: "AEQ Utility TAG", ...


def _receptacle(nema):
    m = re.search(r"(L?\d{1,2}-\d{2})", nema or "")
    return "RECEPTACLE %sR" % m.group(1) if m else "RECEPTACLE, NEMA VERIFY"


def _family_pipes(doc, inst, lvl):
    """Piping connectors of a placed family as [(H / C / D, size in, A.F.F. in)]: the faucet inlets and the
    drain where the family has them, at the height the item was mounted."""
    out = []
    mep = getattr(inst, "MEPModel", None) if inst is not None else None
    if mep is None or mep.ConnectorManager is None:
        return out
    for cn in mep.ConnectorManager.Connectors:
        try:
            svc = _PIPE_SVC.get(str(cn.PipeSystemType))
        except Exception:
            svc = None
        if svc:
            out.append((svc, round(cn.Radius * 24.0, 3), round((cn.Origin.Z - lvl.Elevation) * 12.0, 1)))
    return out


def _roughin_frame(q):
    """The rough-in point on the room face of the wall behind the item and the unit normal into the room
    (inches); an item with no wall behind it roughs in at its centre."""
    if q.get("roughin"):
        return q["roughin"], (q.get("roughin_n") or [0.0, 1.0])
    b = q.get("bbox") or [q["x"], q["y"], q["x"], q["y"]]
    return [(b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0], [0.0, 1.0]


def _aff_text(v):
    v = float(v or 0)
    return '%d"' % int(round(v)) if abs(v - round(v)) < 0.05 else '%s"' % _inch_frac(v)


def utility_rows(doc, layout, takeoff, s):
    """One row per connection, tagged per the template's QF002 format: service + equipment item +
    connection suffix (E4.1, H1.1, C1.2, D1.3). Electrical from the takeoff circuits (what is priced);
    plumbing from the catalog with heights from the placed family's connectors where it has them (faucet
    inlets, drains), else the AEQ rough-in defaults marked VERIFY. An item's connections sit side by side
    on the wall behind it, 8" apart."""
    items = C.catalog(s)["items"]
    rd = C.read_json(os.path.join(s["repo_root"], "config", "program.json"))["rough_in_defaults"]
    lvl = _level(doc)
    sched = {(r["key"], str(r.get("item") or "")): r for r in takeoff["schedule"]}
    circuits = {}
    for c in takeoff["circuits"]:
        circuits.setdefault((c["key"], str(c.get("item") or "")), []).append(c)

    def label(key, item):
        r = sched.get((key, item)) or {}
        return ("%s %s" % (r.get("mfr", ""), r.get("model", ""))).strip() or key

    rows = []
    for q in layout["equipment"]:
        if q["status"] == "demo" or not q["key"] or not q.get("item") or q["layer"].upper().startswith("FS-ELEC"):
            continue
        key, item = q["key"], str(q["item"])
        it = items.get(key, {})
        inst = doc.GetElement(_eid(q["revit_id"])) if q.get("revit_id") else None
        conns = []
        cs = circuits.get((key, item)) or []
        if cs:
            c = dict(cs.pop(0))                  # one circuit per unit, in drawing order
            c["amps"] = c["amps"] or None        # 0 = unknown, not zero amps
            flags = [f for f, bad in (("AMPS", c["amps"] is None), ("BKR", not c["breaker_a"]),
                                      ("NEMA", c["conn"] != "direct" and not re.search(r"\d-\d", c["nema"] or "")))
                     if bad]
            conns.append({"svc": "E-DEVICE" if c["conn"] == "direct" else "E-CORD", "aff": c["height_aff"],
                          "size": "%s-P %sA" % (c["poles"], c["breaker_a"] or "?"),
                          "desc": "J-BOX, DIRECT CONNECT" if c["conn"] == "direct" else "CORD & PLUG, " + _receptacle(c["nema"]),
                          "kw": (it.get("elec") or {}).get("kw"), "amps": c["amps"], "v": c["volts"],
                          "ph": c["phase"] or 1,
                          "remarks": "CKT %s%s" % (c["circuit"], "; VERIFY " + ", ".join(flags) if flags else "")})
        plumb = it.get("plumb") or {}
        fam = _family_pipes(doc, inst, lvl)
        hand = "7-PS" in (it.get("model") or "")
        for ck, svc in (("hw", "H"), ("cw", "C"), ("waste", "D")):
            if not plumb.get(ck):
                continue
            if svc == "D" and plumb.get("indirect"):
                svc = "I"
            hit = [f for f in fam if f[0] == ("D" if svc in ("D", "I") else svc)]
            size = plumb[ck] if plumb[ck] == "VERIFY" else plumb[ck] + '"'
            if svc == "I":
                aff, note = rd.get("floor_sink", 0), "TO FLOOR SINK"
            elif hit and hit[0][2] > 0:
                aff, note = hit[0][2], ""
            else:
                aff = rd["waste_wall_sink"] if svc == "D" else (
                    rd["cw_hw_supply_hand_sink"] if hand else rd["cw_hw_supply_sink"])
                note = "STD HEIGHT - VERIFY"
            conns.append({"svc": svc, "aff": aff, "size": size, "desc": _SVC_DESC[svc], "kw": None, "amps": None,
                          "v": None, "ph": None, "remarks": note})
        if not conns:
            continue
        conns.sort(key=lambda r: _SVC_ORDER.index(r["svc"]))
        p, n = _roughin_frame(q)
        t = (-n[1], n[0])                        # along the wall
        k = len(conns)
        for i, r in enumerate(conns):
            off = (i - (k - 1) / 2.0) * 8.0

            r.update({"item": item, "key": key, "to": label(key, item),
                      "tag": "%s%s.%d" % (r["svc"][0], item, i + 1),
                      "point": _pt([p[0] + t[0] * off, p[1] + t[1] * off]), "normal": n,
                      "wall": bool(q.get("roughin")), "centre": _centre(q), "handle": q["handle"]})
            rows.append(r)
    _group_roughins(rows)
    return rows


def _group_roughins(rows, reach_ft=2.0, oc_in=8.0):
    """Connections of one trade within 2 ft of each other on one wall (an item's own connections, stacked
    units, units side by side) are one rough-in group: spaced 8" on centre about their middle, dimensioned
    once (the first), the others located from it in QF103's remarks."""
    groups = {}
    for r in rows:
        n = r["normal"]
        along_x = abs(n[1]) >= abs(n[0])             # wall runs along X when its normal is along Y
        perp = r["point"].Y if along_x else r["point"].X
        groups.setdefault((r["svc"].startswith("E"), along_x, int(round(perp * 6.0))), []).append(r)
    for (_, along_x, _), lst in groups.items():
        lst.sort(key=lambda r: r["point"].X if along_x else r["point"].Y)
        clusters = []
        for r in lst:
            a = r["point"].X if along_x else r["point"].Y
            if clusters and a - clusters[-1][-1][0] < reach_ft:
                clusters[-1].append((a, r))
            else:
                clusters.append([(a, r)])
        for cl in clusters:
            mid = sum(a for a, _ in cl) / float(len(cl))
            first = cl[0][1]
            for k, (_, r) in enumerate(cl):
                pos = mid + (k - (len(cl) - 1) / 2.0) * oc_in / 12.0
                r["point"] = XYZ(pos, r["point"].Y, 0) if along_x else XYZ(r["point"].X, pos, 0)
                r["dim_first"] = k == 0
                if k:
                    r["remarks"] = '%d" FROM %s%s' % (int(oc_in * k), first["tag"],
                                                       ("; " + r["remarks"]) if r["remarks"] else "")


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
    """Connection symbols from an earlier run (AEQ_CONN_* families) and the old cube markers."""
    out = [i for i in FilteredElementCollector(doc).OfClass(FamilyInstance)
           if i.Symbol.Family.Name.startswith("AEQ_CONN_")]
    out += [d for d in FilteredElementCollector(doc).OfClass(DirectShape)
            if d.LookupParameter(UTIL + " TAG") is not None and d.LookupParameter(UTIL + " TAG").AsString()]
    return out


def utility_markers(doc, rows, lvl, phase):
    """A connection symbol (families.py, coloured per the QF002 legend) at each connection on the room face
    of the wall, turned to the wall; its marker at the rough-in A.F.F. shows in elevations; it carries the
    AEQ Utility parameters (QF103) and the tag text as its Mark. Replaces symbols from an earlier run.
    False when the template has no AEQ Utility parameters."""
    old = _utility_markers(doc)
    if old:
        doc.Delete(List[ElementId]([d.Id for d in old]))
    ok, syms = True, {}
    for r in rows:
        name = "AEQ_CONN_%s" % F._safe(r["svc"])
        sym = syms.get(name) or F.tag_symbol(doc, name)
        if sym is None:
            continue
        syms[name] = sym
        if not sym.IsActive:
            sym.Activate()
        pt = XYZ(r["point"].X, r["point"].Y, lvl.Elevation)
        inst = doc.Create.NewFamilyInstance(pt, sym, lvl, StructuralType.NonStructural)
        ang = math.atan2(r["normal"][1], r["normal"][0]) - math.pi / 2.0
        if abs(ang) > 1e-6:
            ElementTransformUtils.RotateElement(doc, inst.Id, Line.CreateBound(pt, pt + XYZ.BasisZ), ang)
        aff = float(r["aff"] or 0.0) * FT
        _set(inst, "AEQ Marker Top", aff + 3.0 * FT)
        _set(inst, "AEQ Marker Bottom", aff)
        for field, value in (("ITEM", r["item"]), ("TAG", r["tag"]), ("SVC", r["svc"][0]), ("SIZE", r["size"]),
                             ("DESCRIPTION", r["desc"]), ("LOC.", "WALL" if r["wall"] else "AT EQUIP"),
                             ("A.F.F.", _aff_text(r["aff"])), ("SERVICE TO", r["to"]), ("KW", r["kw"]),
                             ("AMPS", r["amps"]), ("V", r["v"]), ("PH", r["ph"]), ("REMARKS", r["remarks"])):
            _set_text(inst, "%s %s" % (UTIL, field), value)
        _set(inst, BuiltInParameter.ALL_MODEL_MARK, r["tag"])
        _set(inst, BuiltInParameter.PHASE_CREATED, phase.Id)
        r["marker"] = inst
        if inst.LookupParameter(UTIL + " TAG") is None:
            ok = False
    return ok


_OLD_NOTE = re.compile(r"^([EP]\d+([/-][EP]\d+)*|(PH|X|E)?\d+(\.\d+)?)$")


def _clear_aeq_tags(doc, view):
    """Drop AEQ tags (and the text-note callouts of older builds) from a view before re-tagging."""
    ids = []
    for t in FilteredElementCollector(doc, view.Id).OfClass(IndependentTag):
        sym = doc.GetElement(t.GetTypeId())
        if sym is not None and getattr(sym, "Family", None) is not None and sym.Family.Name.startswith("AEQ_TAG"):
            ids.append(t.Id)
    ids += [n.Id for n in FilteredElementCollector(doc, view.Id).OfClass(TextNote)
            if _OLD_NOTE.match((n.Text or "").strip())]
    if ids:
        doc.Delete(List[ElementId](ids))


def _leader_end(bb, side, c, inset=3.0):
    """Where an item tag's leader ends: just inside the item's body, on the side facing the tag."""
    if not bb:
        return c
    i = inset * FT
    x = min(max(c.X, bb[0] + i), bb[3] - i)
    y = min(max(c.Y, bb[1] + i), bb[4] - i)
    if side == "N":
        y = bb[4] - i
    elif side == "S":
        y = bb[1] + i
    elif side == "E":
        x = bb[3] - i
    else:
        x = bb[0] + i
    return XYZ(x, y, 0)


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def plan_areas(layout, s, margin_in=60.0):
    """Plan crops (inches). One when the whole kitchen fits the drawing area at the plan scale (1/4", Edgar)
    with room for the tag rows; otherwise the groups of equipment (a gap over 5 ft splits them), each grown
    to the rooms it stands in, so every area plan prints at 1/4" (QF002: split dense plans onto more
    sheets, do not shrink them)."""
    full = room_bbox(layout, margin_in)
    sc = int(s.get("plan_scale", 48))
    if _fit_scale(s, full[2] - full[0], full[3] - full[1]) == sc:
        return [full]
    eq = [q["bbox"] for q in layout["equipment"]
          if q.get("bbox") and q["key"] and q["status"] != "demo" and not q["layer"].upper().startswith("FS-ELEC")]
    if not eq:
        return [full]
    ax = 0 if (full[2] - full[0]) >= (full[3] - full[1]) else 1
    groups = []
    for b in sorted(eq, key=lambda b: b[ax]):
        if groups and b[ax] <= groups[-1]["hi"] + 60.0:
            groups[-1]["boxes"].append(b)
            groups[-1]["hi"] = max(groups[-1]["hi"], b[ax + 2])
        else:
            groups.append({"boxes": [b], "hi": b[ax + 2]})
    if len(groups) < 2:
        return [full]
    rooms = [r["polygon"] for r in layout.get("rooms", [])]
    areas = []
    for g in groups:
        eqbox = _union(g["boxes"])
        box = eqbox
        for poly in rooms:
            rb = [min(p[0] for p in poly), min(p[1] for p in poly), max(p[0] for p in poly), max(p[1] for p in poly)]
            if any(rb[0] <= (b[0] + b[2]) / 2.0 <= rb[2] and rb[1] <= (b[1] + b[3]) / 2.0 <= rb[3] for b in g["boxes"]):
                box = _union([box, rb])
        # the whole room when it still prints at the plan scale, else the equipment with its margin (an empty
        # end of a long room is cropped off rather than shrinking the plan)
        if _fit_scale(s, box[2] - box[0] + 2 * margin_in, box[3] - box[1] + 2 * margin_in) != sc:
            box = eqbox
        box = [box[0] - margin_in, box[1] - margin_in, box[2] + margin_in, box[3] + margin_in]
        areas.append([max(box[0], full[0]), max(box[1], full[1]), min(box[2], full[2]), min(box[3], full[3])])
    return areas


def _cut_high(v, cut_ft=7.0, top_ft=10.0):
    """Plan cut at 7'-0" so wall-hung shelves and other items above 4'-0" show in the equipment plans."""
    from Autodesk.Revit.DB import PlanViewPlane
    try:
        vr = v.GetViewRange()
        if vr.GetOffset(PlanViewPlane.TopClipPlane) < top_ft:
            vr.SetOffset(PlanViewPlane.TopClipPlane, top_ft)
        vr.SetOffset(PlanViewPlane.CutPlane, cut_ft)
        v.SetViewRange(vr)
    except Exception:
        pass


def _hide_zones(doc, view, keep_doors=True):
    """Hide the manufacturers' clearance / service zones (KCL families: QF_Clearances, ..._Proximity,
    ..._Code Mandatory) in a view. Door swings (QF_Clearances_Door_Drawer) stay in plans (keep_doors)."""
    for name in ("OST_SpecialityEquipment", "OST_FoodServiceEquipment", "OST_PlumbingFixtures",
                 "OST_ElectricalEquipment", "OST_MechanicalEquipment", "OST_GenericModel"):
        bic = getattr(BuiltInCategory, name, None)
        cat = None
        try:
            cat = doc.Settings.Categories.get_Item(bic) if bic is not None else None
        except Exception:
            cat = None
        if cat is None:
            continue
        for sub in cat.SubCategories:
            nm = (sub.Name or "").lower()
            if _NOT_BODY.search(nm) and not (keep_doors and ("door" in nm or "drawer" in nm or "swing" in nm)):
                try:
                    view.SetCategoryHidden(sub.Id, True)
                except Exception:
                    pass


def _inside(box_in, pt_ft):
    return box_in[0] <= pt_ft.X * 12.0 <= box_in[2] and box_in[1] <= pt_ft.Y * 12.0 <= box_in[3]


def _svc_filters(doc, marker):
    """View filters on the connection symbols by 'AEQ Utility SVC': electrical (E-...) and the rest
    (plumbing). Made once per model."""
    from Autodesk.Revit.DB import ParameterFilterElement, ElementParameterFilter, ParameterFilterRuleFactory
    p = marker.LookupParameter(UTIL + " SVC") if marker is not None else None
    if p is None:
        return None, None
    pid = p.Id
    cats = List[ElementId]([ElementId(BuiltInCategory.OST_GenericModel)])

    def rule(neg):
        try:
            return (ParameterFilterRuleFactory.CreateNotBeginsWithRule(pid, "E") if neg
                    else ParameterFilterRuleFactory.CreateBeginsWithRule(pid, "E"))
        except Exception:
            return (ParameterFilterRuleFactory.CreateNotBeginsWithRule(pid, "E", False) if neg
                    else ParameterFilterRuleFactory.CreateBeginsWithRule(pid, "E", False))

    out = []
    for name, neg in (("AEQ - Electrical connections", False), ("AEQ - Plumbing connections", True)):
        f = next((x for x in FilteredElementCollector(doc).OfClass(ParameterFilterElement) if x.Name == name), None)
        if f is None:
            f = ParameterFilterElement.Create(doc, name, cats, ElementParameterFilter(rule(neg)))
        out.append(f)
    return out[0], out[1]


# which connection symbols each QF plan shows: (electrical, plumbing)
_SHOWS = {"QF101": (False, False), "QF111": (False, False), "QF201": (False, True), "QF202": (False, True),
          "QF203": (False, False), "QF204": (False, False), "QF301": (True, False), "QF302": (True, False),
          "QF401": (False, False), "QF402": (False, False)}


def _show_connections(doc, view, f_el, f_pl, num):
    """Hide the connection symbols a sheet is not about (on the view template when it controls filters)."""
    if f_el is None:
        return
    el, pl = _SHOWS.get(num, (False, False))
    tgt = doc.GetElement(view.ViewTemplateId) if view.ViewTemplateId != ElementId.InvalidElementId else view
    for f, show in ((f_el, el), (f_pl, pl)):
        for v in (tgt, view):
            try:
                if not v.IsFilterApplied(f.Id):
                    v.AddFilter(f.Id)
                v.SetFilterVisibility(f.Id, show)
                break
            except Exception:
                continue


PLAN_SHEETS = []        # [(sheet number, sheet name, view)] from the last roughin_views run


def _attach_ids(layout, s):
    """Revit ids of the placed items (place_equipment sets them; saved for buttons run one at a time)."""
    if any(q.get("revit_id") for q in layout["equipment"]):
        return
    p = os.path.join(C.store_out(s), "revit_ids.json")
    ids = C.read_json(p) if os.path.isfile(p) else {}
    for q in layout["equipment"]:
        if q["handle"] in ids:
            q["revit_id"] = ids[q["handle"]]


def _perp_faces(doc, axis):
    """Wall side faces that measure along `axis` ('X' or 'Y'): faces of the walls running across it.
    [(coordinate ft, normal sign along axis, reference, wall span on the other axis (lo, hi), width ft)]."""
    from Autodesk.Revit.DB import HostObjectUtils, ShellLayerType, PlanarFace
    out = []
    for w in FilteredElementCollector(doc).OfClass(Wall):
        crv = w.Location.Curve
        a, b = crv.GetEndPoint(0), crv.GetEndPoint(1)
        d = (b - a).Normalize()
        if (axis == "X" and abs(d.Y) < 0.99) or (axis == "Y" and abs(d.X) < 0.99):
            continue
        span = (min(a.Y, b.Y), max(a.Y, b.Y)) if axis == "X" else (min(a.X, b.X), max(a.X, b.X))
        for side in (ShellLayerType.Interior, ShellLayerType.Exterior):
            try:
                refs = HostObjectUtils.GetSideFaces(w, side)
            except Exception:
                continue
            for ref in refs:
                f = w.GetGeometryObjectFromReference(ref)
                if not isinstance(f, PlanarFace):
                    continue
                comp = f.FaceNormal.X if axis == "X" else f.FaceNormal.Y
                if abs(comp) < 0.99:
                    continue
                out.append((f.Origin.X if axis == "X" else f.Origin.Y, 1 if comp > 0 else -1, ref, span, w.Width))
    return out


def _dimension_runs(doc, view, runs, dim_type):
    """QF002 note 4: dimension rough-ins from finished wall faces. runs: [(side, wall-face coordinate ft,
    dimension-line coordinate ft, [(along-wall coordinate ft, marker)])] - one string per run, from the nearest
    wall face before the first connection (a wall running across this one) through every connection's
    centreline."""
    from Autodesk.Revit.DB import ReferenceArray, FamilyInstanceReferenceType
    faces = {"X": None, "Y": None}
    made = 0
    for side, wall_line, line, pts in runs:
        axis = "X" if side in ("N", "S") else "Y"
        if faces[axis] is None:
            faces[axis] = _perp_faces(doc, axis)
        pts = sorted(pts, key=lambda p: p[0])
        first, last = pts[0][0], pts[-1][0]
        meets = [f for f in faces[axis] if f[3][0] - f[4] - 1.5 <= wall_line <= f[3][1] + f[4] + 1.5]
        before = [f for f in meets if f[1] > 0 and f[0] < first - 1.0 / 12.0 and first - f[0] < 40.0]
        after = [f for f in meets if f[1] < 0 and f[0] > last + 1.0 / 12.0 and f[0] - last < 40.0]
        b = max(before, key=lambda f: f[0]) if before else None
        a = min(after, key=lambda f: f[0]) if after else None
        if b is None and a is None:
            continue
        # from whichever wall face is nearer (the nearest corner of the run)
        use = b if (a is None or (b is not None and first - b[0] <= a[0] - last)) else a
        ra = ReferenceArray()
        if use is b:
            ra.Append(use[2])
        for _, m in pts:
            refs = list(m.GetReferences(FamilyInstanceReferenceType.CenterLeftRight))
            if refs:
                ra.Append(refs[0])
        if use is a:
            ra.Append(use[2])
        if ra.Size < 2:
            continue
        lo, hi = (use[0], last + 0.5) if use is b else (first - 0.5, use[0])
        if axis == "X":
            ln = Line.CreateBound(XYZ(lo, line, 0), XYZ(hi, line, 0))
        else:
            ln = Line.CreateBound(XYZ(line, lo, 0), XYZ(line, hi, 0))
        try:
            if dim_type is not None:
                doc.Create.NewDimension(view, ln, ra, dim_type)
            else:
                doc.Create.NewDimension(view, ln, ra)
            made += 1
        except Exception:
            pass
    return made


def _dim_type(doc):
    """The template's AEQ architectural dimension type (3/32" Arial)."""
    from Autodesk.Revit.DB import DimensionType
    for t in FilteredElementCollector(doc).OfClass(DimensionType):
        if _name(t).startswith("AEQ - Architectural"):
            return t
    return None

def roughin_views(doc, layout, takeoff, s):
    """Every QF plan, per plan area (plan_areas: one, or A / B... when the kitchen does not fit at 1/4"),
    cropped and at the plan scale; a connection symbol per connection (QF103 rows), shown only on the sheets
    about it; native tags: utility key tags on QF302 (electrical) and QF202 (plumbing), AEQ item tags on
    QF101 (double bubble for owner / existing items), all in clean rows. Returns [(sheet no., name, view)]."""
    from Autodesk.Revit.DB import ViewDuplicateOption
    _attach_ids(layout, s)
    lvl = _level(doc)
    nc = _phase(doc, "New Construction")
    areas = plan_areas(layout, s)
    sym_eq, sym_other, sym_ut = (F.tag_symbol(doc, F.TAG_EQUIP), F.tag_symbol(doc, F.TAG_EQUIP_OTHERS),
                                 F.tag_symbol(doc, F.TAG_UTIL))
    letters = "ABCDEFGH"
    multi = len(areas) > 1
    out = []
    with _Tx(doc, "AEQ: QF plans + rough-in"):
        dt = _dim_type(doc)
        rows = utility_rows(doc, layout, takeoff, s)
        utility_markers(doc, rows, lvl, nc)
        doc.Regenerate()
        f_el, f_pl = _svc_filters(doc, next((r["marker"] for r in rows if r.get("marker") is not None), None))
        insts = {}
        for q in layout["equipment"]:
            if q.get("revit_id") and q.get("item"):
                inst = doc.GetElement(_eid(q["revit_id"]))
                if inst is not None:
                    insts[q["handle"]] = inst
        room = room_bbox(layout, 0.0)
        for num, title in QF_PLANS:
            base = _qf_plan(doc, num, title, lvl, nc)
            for k, area in enumerate(areas):
                if k == 0:
                    v = base
                else:
                    v = doc.GetElement(base.Duplicate(ViewDuplicateOption.Duplicate))
                sheet_no = num + (letters[k] if multi else "")
                name = title + (" - AREA %s" % letters[k] if multi else "")
                v.Name = _unique_name(doc, ViewPlan, "%s - %s" % (sheet_no, name)) if multi else v.Name
                if v.ViewTemplateId != ElementId.InvalidElementId:
                    # take the AEQ view template's settings, then let go of it: each area plan keeps its
                    # own scale (1/4" wherever it fits) instead of the one the template forces on all
                    v.ApplyViewTemplateParameters(doc.GetElement(v.ViewTemplateId))
                    v.ViewTemplateId = ElementId.InvalidElementId
                _crop(v, *area)
                v.Scale = _fit_scale(s, area[2] - area[0], area[3] - area[1])
                _hide_zones(doc, v)
                _cut_high(v)
                _clear_aeq_tags(doc, v)
                _show_connections(doc, v, f_el, f_pl, num)
                out.append((sheet_no, name, v, area))
        doc.Regenerate()
        for sheet_no, name, v, area in out:
            num = sheet_no[:5]
            if num in ("QF202", "QF302"):
                elec = num == "QF302"
                tags = _TagRows(doc, v, gap_ft=2.0)       # beyond the dimension string
                dims, first = {}, set()
                for r in rows:
                    if r["svc"].startswith("E") != elec or r.get("marker") is None or not _inside(area, r["point"]):
                        continue
                    inst = insts.get(r.get("handle"))
                    vb = inst.get_BoundingBox(v) if inst is not None else None
                    fb = (vb.Min.X, vb.Min.Y, vb.Max.X, vb.Max.Y) if vb else None
                    side, anchor = _tag_side(layout, r["point"], r["centre"], room, front_box=fb)
                    tags.add(r["marker"], sym_ut, r["point"], r["point"], side, anchor)
                    ax = side in ("N", "S")
                    perp = r["point"].Y if ax else r["point"].X          # the wall face the connection is on
                    row = anchor.Y if ax else anchor.X                   # where its tag row is measured from
                    # one string per wall face, beyond the deepest item on it (rows differ with item depth)
                    d = dims.setdefault((side, int(round(perp * 6.0))), [side, perp, row, []])
                    d[2] = max(d[2], row) if side in ("N", "E") else min(d[2], row)
                    if r.get("dim_first"):          # one witness per rough-in group: the rest are 8" O.C. (QF103)
                        d[3].append((r["point"].X if ax else r["point"].Y, r["marker"]))
                runs = [(sd, wall, row + (0.9 if sd in ("N", "E") else -0.9), pts)
                        for sd, wall, row, pts in dims.values() if pts]
                _dimension_runs(doc, v, runs, dt)
                _grow_crop(v, area, tags.place())
            elif num == "QF101":
                tags = _TagRows(doc, v, gap_ft=1.0)
                for q in layout["equipment"]:
                    inst = insts.get(q["handle"])
                    if inst is None:
                        continue
                    bb = _body_box(doc, inst)
                    c = XYZ((bb[0] + bb[3]) / 2.0, (bb[1] + bb[4]) / 2.0, 0) if bb else _centre(q)
                    if not _inside(area, c):
                        continue
                    vb = inst.get_BoundingBox(v)
                    fb = (vb.Min.X, vb.Min.Y, vb.Max.X, vb.Max.Y) if vb else None
                    w = _pt(q["roughin"]) if q.get("roughin") else None
                    side, anchor = (_tag_side(layout, w, _centre(q), room, front_box=fb) if w
                                    else (_side(c, room), c))
                    sym = sym_eq if (q.get("provided_by") or "AEQ") == "AEQ" else sym_other
                    tags.add(inst, sym, c, _leader_end(bb, side, c), side, anchor)
                _grow_crop(v, area, tags.place())
    del PLAN_SHEETS[:]
    PLAN_SHEETS.extend((n, nm, v) for n, nm, v, _ in out)
    return list(PLAN_SHEETS)


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


def _compass(n):
    """Name of the wall an item backs onto, from the normal pointing from that wall into the room."""
    if abs(n[0]) >= abs(n[1]):
        return "WEST" if n[0] > 0 else "EAST"
    return "SOUTH" if n[1] > 0 else "NORTH"


def interior_elevations(doc, layout, st, s):
    """One interior elevation per equipment wall: the run of items backing onto one wall face, looked at from
    the room, cropped to that run (plus 18") and floor to ceiling, item tags above. Returns the views."""
    _attach_ids(layout, s)
    lvl = _level(doc)
    h = (st.get("ceiling_ft") or 10.0)
    plan = next((v for _, _, v in PLAN_SHEETS), None) or _view_named(doc, ViewPlan, "QF101") or _first_plan(doc)
    vft = _vft(doc, ViewFamily.Elevation)
    nc = _phase(doc, "New Construction")
    sc = int(s.get("elev_scale", 48))
    sym_eq, sym_other = F.tag_symbol(doc, F.TAG_EQUIP), F.tag_symbol(doc, F.TAG_EQUIP_OTHERS)
    runs = {}
    for q in layout["equipment"]:
        n = q.get("roughin_n")
        if not q.get("revit_id") or not q.get("roughin") or not n:
            continue
        if abs(n[0]) > 0.9:
            k = ("X", 1 if n[0] > 0 else -1, int(round(q["roughin"][0] / 6.0)))
        elif abs(n[1]) > 0.9:
            k = ("Y", 1 if n[1] > 0 else -1, int(round(q["roughin"][1] / 6.0)))
        else:
            continue
        runs.setdefault(k, []).append(q)
    made = []
    with _Tx(doc, "AEQ: interior elevations"):
        for v in [v for v in FilteredElementCollector(doc).OfClass(View)
                  if not v.IsTemplate and v.Name.startswith(ELEV_SHEET[0] + " ")]:
            try:
                doc.Delete(v.Id)
            except Exception:
                pass
        order = sorted(runs.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2]))
        for idx, (k, qs) in enumerate(order):
            insts = [doc.GetElement(_eid(q["revit_id"])) for q in qs]
            boxes = [_body_box(doc, x) if x is not None else None for x in insts]
            pairs = [(q, i, b) for q, i, b in zip(qs, insts, boxes) if i is not None and b is not None]
            if not pairs:
                continue
            n = qs[0]["roughin_n"]
            w = sum((q["roughin"][0] if k[0] == "X" else q["roughin"][1]) for q in qs) / len(qs) * FT
            if k[0] == "X":
                lo, hi = min(b[1] for _, _, b in pairs), max(b[4] for _, _, b in pairs)
                depth = max(max(abs(b[0] - w), abs(b[3] - w)) for _, _, b in pairs)
            else:
                lo, hi = min(b[0] for _, _, b in pairs), max(b[3] for _, _, b in pairs)
                depth = max(max(abs(b[1] - w), abs(b[4] - w)) for _, _, b in pairs)
            back_off = depth + 1.0          # just in front of the run: the aisle behind the viewer stays out
            mid = (lo + hi) / 2.0
            if k[0] == "X":
                origin = XYZ(w + n[0] * back_off, mid, lvl.Elevation)
            else:
                origin = XYZ(mid, w + n[1] * back_off, lvl.Elevation)
            mk = ElevationMarker.CreateElevationMarker(doc, vft.Id, origin, sc)
            ev = None
            for i in range(4):
                e = mk.CreateElevation(doc, plan.Id, i)
                vd = e.ViewDirection
                if vd.X * n[0] + vd.Y * n[1] > 0.9:
                    ev = e
                    break
                doc.Delete(e.Id)
            if ev is None:
                continue
            labels = []
            for q, _, _ in pairs:
                if str(q.get("item")) not in labels:
                    labels.append(str(q.get("item")))
            ev.Name = _unique_name(doc, type(ev), "%s - ELEVATION %d - %s WALL" % (ELEV_SHEET[0], idx + 1,
                                                                                _compass(n)))
            try:
                ev.get_Parameter(BuiltInParameter.VIEWER_BOUND_OFFSET_FAR).Set(back_off + 1.0)
            except Exception:
                pass
            ev.get_Parameter(BuiltInParameter.VIEW_PHASE).Set(nc.Id)
            ev.AreImportCategoriesHidden = True
            try:
                ev.SetCategoryHidden(ElementId(BuiltInCategory.OST_Levels), True)     # no level heads
                ev.SetCategoryHidden(ElementId(BuiltInCategory.OST_GenericModel), True)  # rough-in markers
            except Exception:
                pass
            _hide_zones(doc, ev, keep_doors=False)
            doc.Regenerate()
            if k[0] == "X":
                _crop_elevation(ev, min(w, origin.X), lo - 1.5, max(w, origin.X), hi + 1.5,
                                lvl.Elevation, lvl.Elevation + h)
            else:
                _crop_elevation(ev, lo - 1.5, min(w, origin.Y), hi + 1.5, max(w, origin.Y),
                                lvl.Elevation, lvl.Elevation + h)
            made.append((ev, pairs, n))
        doc.Regenerate()
        for ev, pairs, n in made:              # item tags in a row above the run, leaders to each item's top
            rd = ev.RightDirection
            top = max(b[5] for _, _, b in pairs)
            zrow = min(top + 1.0, lvl.Elevation + h - 0.75)
            tags = []
            for q, inst, b in pairs:
                sym = sym_eq if (q.get("provided_by") or "AEQ") == "AEQ" else sym_other
                cx, cy = (b[0] + b[3]) / 2.0, (b[1] + b[4]) / 2.0
                try:
                    t = IndependentTag.Create(doc, sym.Id, ev.Id, Reference(inst), False, TagOrientation.Horizontal,
                                              XYZ(cx, cy, zrow))
                except Exception:
                    continue
                fx = b[3] if n[0] > 0.5 else (b[0] if n[0] < -0.5 else cx)       # the face toward the viewer
                fy = b[4] if n[1] > 0.5 else (b[1] if n[1] < -0.5 else cy)
                tags.append((t, XYZ(cx, cy, 0).DotProduct(rd), XYZ(fx, fy, b[5] - 2.0 * FT)))
            if not tags:
                continue
            doc.Regenerate()
            tags.sort(key=lambda x: x[1])
            items = []
            for t, ideal, _ in tags:
                bb = t.get_BoundingBox(ev)
                half = abs((bb.Max - bb.Min).DotProduct(rd)) / 2.0 if bb else 0.2
                items.append((ideal, half, half))
            pos = _spread(items, 0.15)
            for (t, ideal, end), p in zip(tags, pos):
                t.TagHeadPosition = t.TagHeadPosition + rd.Multiply(p - ideal)
                try:
                    t.HasLeader = True
                    t.LeaderEndCondition = LeaderEndCondition.Free
                    t.SetLeaderEnd(list(t.GetTaggedReferences())[0], end)
                except Exception:
                    pass
    return [ev for ev, _, _ in made]


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


def _by_item(vs):
    """One row per item number with its count (like Edgar's QF101-R1 schedule), not one row per unit."""
    d = vs.Definition
    mark = next((d.GetField(i) for i in range(d.GetFieldCount()) if d.GetField(i).GetName() == "Mark"), None)
    if mark is not None and not any(d.GetSortGroupField(i).FieldId == mark.FieldId
                                    for i in range(d.GetSortGroupFieldCount())):
        d.InsertSortGroupField(ScheduleSortGroupField(mark.FieldId), 0)
    d.IsItemized = False


def equipment_schedule(doc):
    """QF102: the template's AEQ equipment schedule when its category covers all placed equipment;
    otherwise one built here with the same fields (multi-category when equipment spans categories)."""
    cats = {}
    for inst in FilteredElementCollector(doc).OfClass(FamilyInstance):
        k = _key_of(doc, inst)
        if k and inst.Category and re.match(r"^[A-Z0-9_x]+$", k) and inst.Category.Name != "Generic Models":
            cats[_id_int(inst.Category.Id)] = inst.Category.Id
    if not cats:
        return None
    tpl = _view_named(doc, ViewSchedule, "AEQ - QF102")
    if tpl is not None and list(cats) == [_id_int(tpl.Definition.CategoryId)]:
        with _Tx(doc, "AEQ: equipment schedule by item"):
            _by_item(tpl)
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
        _by_item(vs)
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


TITLE_W = 3.8 / 12.0        # a view title is wider than a narrow view: room for it so titles never overlap


def _size(vp):
    o = vp.GetBoxOutline()
    return max(o.MaximumPoint.X - o.MinimumPoint.X, TITLE_W), o.MaximumPoint.Y - o.MinimumPoint.Y


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


def _scale_marks(sc):
    """Graphic scale divisions in feet for a view scale (1/4" -> 0, 2, 4, 8)."""
    unit = max(1, int(round(2.0 * sc / 48.0)))
    return [0, unit, 2 * unit, 4 * unit]


def _north_and_scale(doc, sh, sc, s):
    """North arrow and graphic scale in the lower right of the drawing area (QF002 note 8: north arrows and
    graphic scales go with the views they describe)."""
    x0, y0, x1, y1 = [a / 12.0 for a in _area(s)]
    tn = _text_type(doc, "AEQ - Notes - 3/32")
    na = next((x for x in FilteredElementCollector(doc).OfClass(FamilySymbol) if x.Family.Name == "North Arrow 2"),
              None)
    if na is not None:
        if not na.IsActive:
            na.Activate()
        try:
            doc.Create.NewFamilyInstance(XYZ(x1 - 0.45 / 12.0, y0 + 0.75 / 12.0, 0), na, sh)
        except Exception:
            pass
    marks = _scale_marks(sc)
    L = marks[-1] / float(sc)                       # sheet feet
    xs = x1 - 0.9 / 12.0 - L
    yb = y0 + 0.2 / 12.0
    hgt = 0.05 / 12.0
    for a, b in ((XYZ(xs, yb, 0), XYZ(xs + L, yb, 0)), (XYZ(xs, yb + hgt, 0), XYZ(xs + L, yb + hgt, 0))):
        doc.Create.NewDetailCurve(sh, Line.CreateBound(a, b))
    for m in marks:
        x = xs + m / float(sc)
        doc.Create.NewDetailCurve(sh, Line.CreateBound(XYZ(x, yb, 0), XYZ(x, yb + hgt, 0)))
        TextNote.Create(doc, sh.Id, XYZ(x - 0.03 / 12.0, yb + hgt + 0.13 / 12.0, 0), "%d'" % m if m else "0",
                        TextNoteOptions(tn))


def _print_size(v):
    """Printed size of a view (inches): crop box over the scale."""
    cb = v.CropBox
    return ((cb.Max.X - cb.Min.X) * 12.0 / v.Scale, (cb.Max.Y - cb.Min.Y) * 12.0 / v.Scale)


def _pack(views, s, title_in=0.6, gap_in=0.5):
    """Shelf-pack views onto as many sheets as needed at their own scale: [[view, ...], ...]."""
    x0, y0, x1, y1 = _area(s)
    W, H = x1 - x0, y1 - y0
    sheets, rows = [], []
    for v in views:
        w, h = _print_size(v)
        w = max(w, TITLE_W * 12.0)
        h += title_in
        placed = False
        for row in rows:
            if row["w"] + gap_in + w <= W:
                row["v"].append(v)
                row["w"] += gap_in + w
                row["h"] = max(row["h"], h)
                placed = True
                break
        if not placed:
            if rows and sum(r["h"] for r in rows) + h > H:
                sheets.append([x for r in rows for x in r["v"]])
                rows = []
            rows.append({"v": [v], "w": w, "h": h})
    if rows:
        sheets.append([x for r in rows for x in r["v"]])
    return sheets


def make_sheets(doc, s):
    """Lay the build onto the template's QF sheets: each plan (per area: QF101A, QF101B... when split) on its
    sheet with a north arrow and graphic scale; the per-wall interior elevations at 1/4" on QF403 (QF404...
    when they need more room); QF502 3D; and an equipment schedule built here (if any) in place of the
    template's on QF102. Returns the sheets touched."""
    tb = _titleblock(doc, s.get("titleblock_name"))
    sheets = dict((sh.SheetNumber, sh) for sh in FilteredElementCollector(doc).OfClass(ViewSheet))
    made = []
    ref = sheets.get(QF_PLANS[0][0]) or next(iter(sheets.values()), None)

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
        plans = list(PLAN_SHEETS)
        if not plans:                           # button 6 on its own: the QF plans by their view names
            nums = [n for n, _ in QF_PLANS]
            for v in FilteredElementCollector(doc).OfClass(ViewPlan):
                m = re.match(r"^(QF\d{3}[A-Z]?) - (.+)$", v.Name)
                if not v.IsTemplate and m and m.group(1)[:5] in nums:
                    plans.append((m.group(1), m.group(2), v))
            plans.sort(key=lambda x: x[0])
        taken = set()
        for sheet_no, name, v in plans:
            if v is None:
                continue
            base = sheet_no[:5]
            if sheet_no not in sheets and sheet_no != base and base in sheets and base not in taken:
                sh0 = sheets.pop(base)                  # area A takes over the template's sheet
                sh0.SheetNumber, sh0.Name = sheet_no, name
                sheets[sheet_no] = sh0
                taken.add(base)
            sh = sheet(sheet_no, name)
            _place(doc, sh, [v], s, rescale=False)
            _north_and_scale(doc, sh, v.Scale, s)
        elevs = [v for v in FilteredElementCollector(doc).OfClass(View)
                 if not v.IsTemplate and v.Name.startswith(ELEV_SHEET[0] + " - ELEVATION")]
        if elevs:
            elevs.sort(key=lambda v: int(re.search(r"ELEVATION (\d+)", v.Name).group(1)))
            for v in elevs:
                _set_scale(doc, v, int(s.get("elev_scale", 48)))
            doc.Regenerate()
            for gi, grp in enumerate(_pack(elevs, s)):
                num = "QF%03d" % (403 + gi)
                _place(doc, sheet(num, ELEV_SHEET[1] if gi == 0 else ELEV_SHEET[1] + " (CONT.)"), grp, s,
                       rescale=False)
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
