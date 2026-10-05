# -*- coding: utf-8 -*-
"""Families the build makes itself (Revit 2027, pyRevit IronPython 2.7).

- Equipment from Edgar's DWG blocks when there is no KCL Revit family (Edgar: "use the characters I
  provided"). KCL 3D blocks (polyface meshes) become the family's real geometry, merged into flat faces so
  plans and elevations show the object's edges, not its triangles. 2D AutoQuotes blocks give the plan
  linework exactly as drawn, plus a body (table, shelf, shelving, box) for elevations and 3D. The block base
  point is the family origin, so an instance placed at the block's insertion point and rotation lands
  exactly where the DWG has it.
- AEQ tags, made from the template's Door Tag (its Mark label in the rounded bubble of Edgar's QF101-R1):
  equipment item tag (Specialty Equipment), a double bubble for items by others (owner / existing), and
  the utility key tag (Generic Models, serif text per the QF002 tag format, no outline).
- Connection symbols, one Generic Model family per service, coloured per the QF002 utility legend. The
  symbol sits on the room face of the wall in plan; a small marker at the rough-in height shows in
  elevations; the AEQ Utility parameters on the instance feed the QF103 schedule.

Families are written to <work_dir>/_families and loaded into the store model.
"""
import os
import re

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (Arc, BuiltInCategory, BuiltInParameter, Category, Color, CurveArray, CurveArrArray,
                               DirectShape, Element, ElementId, FamilyElementVisibility,
                               FamilyElementVisibilityType, FamilySymbol, FilteredElementCollector,
                               GeometryObject, GraphicsStyleType, IFamilyLoadOptions, FamilySource, Line, Plane,
                               SaveAsOptions, SketchPlane, TessellatedFace, TessellatedShapeBuilder,
                               TessellatedShapeBuilderFallback, TessellatedShapeBuilderTarget, Transaction, View,
                               XYZ, CurveElement)
from System.Collections.Generic import IList, List

FT = 1.0 / 12.0
TAG_EQUIP = "AEQ_TAG_EQUIPMENT"
TAG_EQUIP_OTHERS = "AEQ_TAG_EQUIPMENT_BY_OTHERS"
TAG_UTIL = "AEQ_TAG_UTILITY"

# QF002 utility legend: service -> (line colour, plan symbol)
SERVICES = {
    "E-CORD": ((200, 0, 200), "duplex"),
    "E-DISC": ((70, 110, 230), "disc"),
    "E-DEVICE": ((110, 110, 110), "jbox"),
    "C": ((0, 150, 220), "supply"),
    "H": ((0, 150, 220), "supply_hot"),
    "D": ((215, 0, 0), "waste"),
    "I": ((0, 150, 70), "indirect"),
    "G": ((235, 130, 0), "gas"),
}


class LoadOpts(IFamilyLoadOptions):
    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        overwriteParameterValues.Value = True
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        source.Value = FamilySource.Family
        overwriteParameterValues.Value = True
        return True


def _rft(app, name):
    base = app.FamilyTemplatePath
    for sub in ("English-Imperial", "English", ""):
        p = os.path.join(base, sub, name) if sub else os.path.join(base, name)
        if os.path.isfile(p):
            return p
    raise IOError("Revit family template not found: %s (under %s)" % (name, base))


def _safe(s):
    return re.sub(r"[^A-Za-z0-9]+", "_", s or "").strip("_")


def _family(doc, name):
    for s in FilteredElementCollector(doc).OfClass(FamilySymbol):
        if s.Family.Name == name:
            return s.Family
    return None


def _save_load(fd, doc, path):
    so = SaveAsOptions()
    so.OverwriteExistingFile = True
    fd.SaveAs(path, so)
    fd.LoadFamily(doc, LoadOpts())


# ------------------------------------------------------------------ geometry helpers
def _seg(fd, a, b, sp, style=None):
    if a.DistanceTo(b) < fd.Application.ShortCurveTolerance:
        return None
    c = fd.FamilyCreate.NewSymbolicCurve(Line.CreateBound(a, b), sp)
    if style is not None:
        c.LineStyle = style
    return c


def _arc(fd, c, r, a0, a1, sp, style=None):
    import math
    arc = Arc.Create(c, r, a0, a1, XYZ.BasisX, XYZ.BasisY)
    e = fd.FamilyCreate.NewSymbolicCurve(arc, sp)
    if style is not None:
        e.LineStyle = style
    return e


def _plan_lines(fd, curves, sp):
    """The block's 2D linework as symbolic lines (plan only). Flattened arcs come as point runs: points
    closer than Revit's short-curve tolerance are merged so nothing is dropped."""
    tol = fd.Application.ShortCurveTolerance * 1.01
    n = 0
    for c in curves:
        pts = [(c[1], c[2]), (c[3], c[4])] if c[0] == "L" else [tuple(p) for p in c[1]]
        run = [XYZ(pts[0][0] * FT, pts[0][1] * FT, 0)]
        for x, y in pts[1:]:
            p = XYZ(x * FT, y * FT, 0)
            if p.DistanceTo(run[-1]) >= tol:
                run.append(p)
        for a, b in zip(run, run[1:]):
            if _seg(fd, a, b, sp) is not None:
                n += 1
    return n


def _box(fd, x0, y0, x1, y1, z0, z1, hide_in_plan):
    """An extruded box (inches) for 3D / elevations; hidden in plan when the block's own linework is
    the plan symbol."""
    if x1 - x0 < 0.05 or y1 - y0 < 0.05 or z1 - z0 < 0.05:
        return None
    sp = SketchPlane.Create(fd, Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ(0, 0, z0 * FT)))
    p = [XYZ(x0 * FT, y0 * FT, z0 * FT), XYZ(x1 * FT, y0 * FT, z0 * FT), XYZ(x1 * FT, y1 * FT, z0 * FT),
         XYZ(x0 * FT, y1 * FT, z0 * FT)]
    ca = CurveArray()
    for i in range(4):
        ca.Append(Line.CreateBound(p[i], p[(i + 1) % 4]))
    caa = CurveArrArray()
    caa.Append(ca)
    ext = fd.FamilyCreate.NewExtrusion(True, caa, sp, (z1 - z0) * FT)
    if hide_in_plan:
        vis = FamilyElementVisibility(FamilyElementVisibilityType.Model)
        vis.IsShownInTopBottom = False
        ext.SetVisibility(vis)
    return ext


def _body(fd, g, it, back_local):
    """A body for a 2D block: catalog shape (table / shelf / shelving / box) over the block's outline."""
    bx = g.get("body_bbox") or g.get("bbox")
    if not bx:
        return 0
    x0, y0, x1, y1 = bx
    shape = it.get("shape") or {}
    kind = shape.get("kind") or ("table" if it.get("stack") == "base" and it.get("category") == "nonutility" else "box")
    mount = (it.get("mount") or {}).get("aff_in") or 0.0
    hide = bool(g.get("curves"))
    n = 0
    if kind == "table":
        top = shape.get("top_in") or it.get("height_in") or 34.0
        n += bool(_box(fd, x0, y0, x1, y1, top - 1.5, top, hide))
        leg = 1.625
        for lx, ly in ((x0 + 1, y0 + 1), (x1 - 1 - leg, y0 + 1), (x0 + 1, y1 - 1 - leg), (x1 - 1 - leg, y1 - 1 - leg)):
            n += bool(_box(fd, lx, ly, lx + leg, ly + leg, 0.0, top - 1.5, hide))
        sh = shape.get("splash_in") or 0.0
        if sh and back_local:
            bxx, byy = back_local
            if abs(byy) >= abs(bxx):
                yy = y1 if byy > 0 else y0
                n += bool(_box(fd, x0, min(yy, yy - byy), x1, max(yy, yy - byy), top, top + sh, hide))
            else:
                xx = x1 if bxx > 0 else x0
                n += bool(_box(fd, min(xx, xx - bxx), y0, max(xx, xx - bxx), y1, top, top + sh, hide))
    elif kind == "shelf":
        th = shape.get("thick_in") or 1.5
        n += bool(_box(fd, x0, y0, x1, y1, mount, mount + th, hide))
    elif kind == "shelving":
        h = (g.get("z") or [0, it.get("height_in") or 74.0])[1] or it.get("height_in") or 74.0
        k = shape.get("shelves") or 4
        post = 1.0
        for px, py in ((x0, y0), (x1 - post, y0), (x0, y1 - post), (x1 - post, y1 - post)):
            n += bool(_box(fd, px, py, px + post, py + post, 0.0, h, False))
        for i in range(k):
            z = 6.0 + (h - 7.0) * i / float(max(k - 1, 1))
            n += bool(_box(fd, x0, y0, x1, y1, z, z + 1.0, False))
    else:
        h = shape.get("height_in") or it.get("height_in") or 34.0
        n += bool(_box(fd, x0, y0, x1, y1, mount, mount + h, hide))
    return n


def _mesh(fd, solids):
    """KCL mesh pieces -> one DirectShape of the family's category (the family's real 3D geometry)."""
    objs = List[GeometryObject]()
    for sol in solids:
        b = TessellatedShapeBuilder()
        b.OpenConnectedFaceSet(False)
        added = 0
        for face in sol:
            loops = List[IList[XYZ]]()
            for lp in face:
                loops.Add(List[XYZ]([XYZ(v[0] * FT, v[1] * FT, v[2] * FT) for v in lp]))
            try:
                b.AddFace(TessellatedFace(loops, ElementId.InvalidElementId))
                added += 1
            except Exception:
                pass
        if not added:
            continue
        b.CloseConnectedFaceSet()
        b.Target = TessellatedShapeBuilderTarget.AnyGeometry
        b.Fallback = TessellatedShapeBuilderFallback.Mesh
        try:
            b.Build()
            for go in b.GetBuildResult().GetGeometricalObjects():
                objs.Add(go)
        except Exception:
            pass
    if objs.Count:
        ds = DirectShape.CreateElement(fd, ElementId(BuiltInCategory.OST_SpecialityEquipment))
        ds.SetShape(objs)
    return objs.Count


def _type_params(fd, it, key):
    fm = fd.FamilyManager
    if fm.CurrentType is None:
        fm.NewType(it.get("model") or key)
    for bip, val in ((BuiltInParameter.ALL_MODEL_MANUFACTURER, it.get("mfr")),
                     (BuiltInParameter.ALL_MODEL_MODEL, it.get("model")),
                     (BuiltInParameter.ALL_MODEL_DESCRIPTION, it.get("description"))):
        fp = fm.get_Parameter(bip)
        if fp is not None and val:
            try:
                fm.Set(fp, val)
            except Exception:
                pass


def block_family(app, doc, out_dir, key, block_name, g, it, back_local=None):
    """Make (or remake) the family for an item drawn with `block_name` and load it into doc. Returns
    (family name, note). doc must have no open transaction."""
    name = "AEQ_%s_%s" % (_safe(key), _safe(block_name)[:8])
    path = os.path.join(out_dir, name + ".rfa")
    fd = app.NewFamilyDocument(_rft(app, "Specialty Equipment.rft"))
    note = ""
    try:
        t = Transaction(fd, "AEQ block family")
        t.Start()
        if g.get("solids"):
            note = "%d mesh pieces from the DWG block" % _mesh(fd, g["solids"])
        else:
            sp = SketchPlane.Create(fd, Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ.Zero))
            nl = _plan_lines(fd, g.get("curves") or [], sp)
            nb = _body(fd, g, it, back_local)
            note = "%d plan lines from the DWG block, %d body pieces" % (nl, nb)
        _type_params(fd, it, key)
        t.Commit()
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir)
        _save_load(fd, doc, path)
    finally:
        fd.Close(False)
    return name, note


# ------------------------------------------------------------------------------ tags
def _owner_view(fd):
    for v in FilteredElementCollector(fd).OfClass(View):
        if not v.IsTemplate:
            return v
    return None


def tag_families(app, doc, out_dir):
    """Equipment item tag, its 'by others' double bubble, and the utility key tag, from the template's Door
    Tag (Mark label, rounded bubble, Arial 3/32", no arrowhead). Loaded into doc. Returns the names made."""
    door = next((s for s in FilteredElementCollector(doc).OfClass(FamilySymbol) if s.Family.Name == "Door Tag"), None)
    if door is None:
        raise Exception("Template has no Door Tag family to make the AEQ tags from")
    specs = [(TAG_EQUIP, BuiltInCategory.OST_SpecialityEquipmentTags, "bubble"),
             (TAG_EQUIP_OTHERS, BuiltInCategory.OST_SpecialityEquipmentTags, "double"),
             (TAG_UTIL, BuiltInCategory.OST_GenericModelTags, "text")]
    made = []
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    for name, bic, style in specs:
        fd = doc.EditFamily(door.Family)
        try:
            t = Transaction(fd, "AEQ tag")
            t.Start()
            fd.OwnerFamily.FamilyCategory = Category.GetCategory(fd, bic)
            lines = [e for e in FilteredElementCollector(fd).OfClass(CurveElement)]
            for e in FilteredElementCollector(fd).WhereElementIsNotElementType():
                if e.GetType().Name == "AnnotationLabel":
                    lt = fd.GetElement(e.GetTypeId())
                    bg = lt.get_Parameter(BuiltInParameter.TEXT_BACKGROUND)
                    if bg is not None and not bg.IsReadOnly:
                        bg.Set(0)                       # opaque: lines behind the number don't run through it
                    if style == "text":                 # QF002: a serif utility-tag font so I reads apart from 1
                        lt.get_Parameter(BuiltInParameter.TEXT_FONT).Set("Times New Roman")
            if style == "text":
                fd.Delete(List[ElementId]([e.Id for e in lines]))
            elif style == "double":
                v = _owner_view(fd)
                r, w = 0.0938 / 12.0, 0.0938 / 12.0
                o = r + 0.022 / 12.0
                for y in (o, -o):
                    fd.FamilyCreate.NewDetailCurve(v, Line.CreateBound(XYZ(-w, y, 0), XYZ(w, y, 0)))
                import math
                fd.FamilyCreate.NewDetailCurve(v, Arc.Create(XYZ(w, 0, 0), o, -math.pi / 2, math.pi / 2,
                                                             XYZ.BasisX, XYZ.BasisY))
                fd.FamilyCreate.NewDetailCurve(v, Arc.Create(XYZ(-w, 0, 0), o, math.pi / 2, 3 * math.pi / 2,
                                                             XYZ.BasisX, XYZ.BasisY))
            t.Commit()
            _save_load(fd, doc, os.path.join(out_dir, name + ".rfa"))
            made.append(name)
        finally:
            fd.Close(False)
    t = Transaction(doc, "AEQ tag types")
    t.Start()
    for s in FilteredElementCollector(doc).OfClass(FamilySymbol):
        if s.Family.Name in (TAG_EQUIP, TAG_EQUIP_OTHERS, TAG_UTIL):
            p = s.get_Parameter(BuiltInParameter.LEADER_ARROWHEAD)
            if p is not None and not p.IsReadOnly:
                p.Set(ElementId.InvalidElementId)          # no arrowhead: the leader just meets the item
            if not s.IsActive:
                s.Activate()
    t.Commit()
    return made


def tag_symbol(doc, name):
    for s in FilteredElementCollector(doc).OfClass(FamilySymbol):
        if s.Family.Name == name:
            return s
    return None


# ------------------------------------------------------------------ connection symbols
def _conn_symbol(fd, shape, sp, style):
    """Plan symbol, local X along the wall, +Y into the room, origin on the wall face (inches)."""
    import math

    def P(x, y):
        return XYZ(x * FT, y * FT, 0)

    def circle(cx, cy, r):
        _arc(fd, P(cx, cy), r * FT, 0, math.pi, sp, style)
        _arc(fd, P(cx, cy), r * FT, math.pi, 2 * math.pi, sp, style)

    if shape == "duplex":                       # receptacle: circle, two lines parallel to the wall
        circle(0, 3.5, 3.0)
        for y in (2.5, 4.5):
            _seg(fd, P(-4.5, y), P(4.5, y), sp, style)
    elif shape == "disc":                       # disconnect: square with a diagonal
        for a, b in (((-3, 0.5), (3, 0.5)), ((3, 0.5), (3, 6.5)), ((3, 6.5), (-3, 6.5)), ((-3, 6.5), (-3, 0.5)),
                     ((-3, 0.5), (3, 6.5))):
            _seg(fd, P(*a), P(*b), sp, style)
    elif shape == "jbox":                       # junction box / device: circle with a cross
        circle(0, 3.5, 3.0)
        _seg(fd, P(-3, 3.5), P(3, 3.5), sp, style)
        _seg(fd, P(0, 0.5), P(0, 6.5), sp, style)
    elif shape in ("supply", "supply_hot"):     # water: circle (hot: with a bar)
        circle(0, 2.5, 2.0)
        if shape == "supply_hot":
            _seg(fd, P(0, 0.5), P(0, 4.5), sp, style)
    elif shape == "waste":                      # direct waste: circle with an X
        circle(0, 3.0, 2.5)
        _seg(fd, P(-1.77, 1.23), P(1.77, 4.77), sp, style)
        _seg(fd, P(-1.77, 4.77), P(1.77, 1.23), sp, style)
    elif shape == "indirect":                   # indirect waste: square with an X
        for a, b in (((-2.5, 0.5), (2.5, 0.5)), ((2.5, 0.5), (2.5, 5.5)), ((2.5, 5.5), (-2.5, 5.5)),
                     ((-2.5, 5.5), (-2.5, 0.5)), ((-2.5, 0.5), (2.5, 5.5)), ((-2.5, 5.5), (2.5, 0.5))):
            _seg(fd, P(*a), P(*b), sp, style)
    else:                                       # gas: triangle
        for a, b in (((-3, 0.5), (3, 0.5)), ((3, 0.5), (0, 5.7)), ((0, 5.7), (-3, 0.5))):
            _seg(fd, P(*a), P(*b), sp, style)


def connection_families(app, doc, out_dir):
    """AEQ_CONN_<service>: plan symbol in the service colour + a 3" marker whose height is the instance
    parameter 'AEQ Marker Bottom' (the rough-in A.F.F.), so it shows in elevations at the right height while
    the instance stays on the level (always in plan). Returns {service: family name}."""
    out = {}
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    for svc, (rgb, shape) in sorted(SERVICES.items()):
        name = "AEQ_CONN_%s" % _safe(svc)
        out[svc] = name
        fd = app.NewFamilyDocument(_rft(app, "Generic Model.rft"))
        try:
            t = Transaction(fd, "AEQ connection")
            t.Start()
            cat = fd.OwnerFamily.FamilyCategory
            sub = fd.Settings.Categories.NewSubcategory(cat, "AEQ Connection %s" % svc)
            sub.LineColor = Color(rgb[0], rgb[1], rgb[2])
            sub.SetLineWeight(3, GraphicsStyleType.Projection)
            style = sub.GetGraphicsStyle(GraphicsStyleType.Projection)
            sp = SketchPlane.Create(fd, Plane.CreateByNormalAndOrigin(XYZ.BasisZ, XYZ.Zero))
            _conn_symbol(fd, shape, sp, style)
            fm = fd.FamilyManager
            if fm.CurrentType is None:
                fm.NewType(svc)
            from Autodesk.Revit.DB import SpecTypeId, GroupTypeId
            try:
                bottom = fm.AddParameter("AEQ Marker Bottom", GroupTypeId.Geometry, SpecTypeId.Length, True)
                top = fm.AddParameter("AEQ Marker Top", GroupTypeId.Geometry, SpecTypeId.Length, True)
            except Exception:
                bottom = top = None
            ext = _box(fd, -1.5, 0.0, 1.5, 3.0, 0.0, 3.0, True)
            if ext is not None:
                ext.Subcategory = sub
                if bottom is not None:
                    fm.AssociateElementParameterToFamilyParameter(
                        ext.get_Parameter(BuiltInParameter.EXTRUSION_START_PARAM), bottom)
                    fm.AssociateElementParameterToFamilyParameter(
                        ext.get_Parameter(BuiltInParameter.EXTRUSION_END_PARAM), top)
            t.Commit()
            _save_load(fd, doc, os.path.join(out_dir, name + ".rfa"))
        finally:
            fd.Close(False)
    return out
