# -*- coding: utf-8 -*-
"""Revit 2026 model/sheet builder for the PH / Cinemark retrofit (pyRevit, IronPython 2.7).

Every function takes the active Document and does its own Transaction, so each
ribbon button is one undoable step. Geometry from layout.json is inches; Revit
internal units are feet.

NOT YET RUN IN REVIT: written against the Revit 2026 API without a Revit
session. Expect first-run fixes on the PC (see docs/REVIT_WORKFLOW.md, "first run").
"""
import math
import os
import re
import datetime

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import (BuiltInCategory, BuiltInParameter, BoundingBoxXYZ, Color, DisplayStyle,
                               DWGImportOptions, ElementId, ElementTransformUtils, ElevationMarker,
                               FamilySymbol, FilteredElementCollector, ImageExportOptions, ImageFileType,
                               ImageResolution, ImportPlacement, ImportUnit, Level, Line, OverrideGraphicSettings,
                               PDFExportOptions, Phase, SaveAsOptions, ScheduleSheetInstance, ScheduleSortGroupField,
                               StorageType, TextNote, TextNoteLeaderTypes, TextNoteOptions, TextNoteType,
                               Transaction, Transform, View, View3D, ViewDrafting, ViewFamily, ViewFamilyType,
                               ViewOrientation3D, ViewPlan, ViewSchedule, ViewSheet, Viewport, Wall, WallKind,
                               WallType, XYZ, ExportRange, FitDirectionType, ZoomFittype, IFamilyLoadOptions,
                               FamilySource, FamilyInstance)
from Autodesk.Revit.DB.Structure import StructuralType
from System.Collections.Generic import List

from aeq_cinemark import config as C

FT = 1.0 / 12.0   # inches -> feet


# ---------------------------------------------------------------- helpers
def _pt(xy, z=0.0):
    return XYZ(xy[0] * FT, xy[1] * FT, z)


def _first(doc, cls):
    return FilteredElementCollector(doc).OfClass(cls).FirstElement()


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
    poly = layout["rooms"][0]["polygon"] if layout.get("rooms") else None
    if poly:
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    else:
        pts = [w["start"] for w in layout["walls"]] + [w["end"] for w in layout["walls"]]
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
        _set(pi, BuiltInParameter.PROJECT_NAME, "Pizza Hut Kitchen Conversion - Cinemark #%d %s" % (
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
    basics = [w for w in FilteredElementCollector(doc).OfClass(WallType) if w.Kind == WallKind.Basic]
    if not basics:
        raise Exception("Template has no basic wall types")
    best = min(basics, key=lambda w: abs(w.Width - thk_in * FT))
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
    sym = next((x for x in syms if type_name and x.Name == type_name), syms[0])
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


def place_equipment(doc, layout, catalog, s):
    items = catalog["items"]
    lvl = _level(doc)
    nc = _phase(doc, "New Construction")
    placed, skipped = [], []
    with _Tx(doc, "AEQ: place equipment"):
        for q in layout["equipment"]:
            if q["status"] == "demo" or not q["key"]:
                continue
            it = items.get(q["key"], {})
            if not it.get("rfa"):
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
                host = _nearest_wall(doc, p)
                inst = doc.Create.NewFamilyInstance(p, sym, host, lvl, StructuralType.NonStructural)
            if abs(q.get("rotation", 0)) > 0.01:
                ElementTransformUtils.RotateElement(doc, inst.Id, Line.CreateBound(p, p + XYZ.BasisZ),
                                                    math.radians(q["rotation"]))
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
            placed.append((q["key"], inst.Id.IntegerValue))
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
        out[key] = {"revit_family": sym.Family.Name, "revit_type": sym.Name, "params": params,
                    "elec": _parse_elec(params), "connectors": conns,
                    "synced_at": datetime.datetime.now().isoformat()[:19]}
    path = os.path.join(s["repo_root"], "config", "families_synced.json")
    prev = C.read_json(path) if os.path.isfile(path) else {}
    prev.update(out)
    C.write_json(path, prev)
    return path, sorted(out)


# ---------------------------------------------------- 5. views + rough-in
def _crop(view, x0, y0, x1, y1):
    bb = BoundingBoxXYZ()
    bb.Min = XYZ(x0 * FT, y0 * FT, -10)
    bb.Max = XYZ(x1 * FT, y1 * FT, 10)
    view.CropBoxActive = True
    view.CropBoxVisible = False
    view.CropBox = bb


def _plan(doc, name, lvl, bbox, scale):
    v = ViewPlan.Create(doc, _vft(doc, ViewFamily.FloorPlan).Id, lvl.Id)
    v.Name = _unique_name(doc, ViewPlan, name)
    v.Scale = scale
    _crop(v, *bbox)
    try:
        v.get_Parameter(BuiltInParameter.VIEW_PHASE).Set(_phase(doc, "New Construction").Id)
    except Exception:
        pass
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


def _equip_by_mark(doc):
    out = {}
    for inst in FilteredElementCollector(doc).OfClass(FamilyInstance):
        c = inst.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        m = inst.get_Parameter(BuiltInParameter.ALL_MODEL_MARK)
        if c and c.AsString():
            bb = inst.get_BoundingBox(None)
            if bb:
                mark = (m.AsString() if m else None) or ""
                out.setdefault((c.AsString(), mark), []).append((bb.Min + bb.Max) * 0.5)
    return out


def _note(doc, view, at, target, text):
    tn_type = FilteredElementCollector(doc).OfClass(TextNoteType).FirstElementId()
    n = TextNote.Create(doc, view.Id, at, text, TextNoteOptions(tn_type))
    if target is not None:
        ld = n.AddLeader(TextNoteLeaderTypes.TNLT_STRAIGHT_R)
        ld.End = XYZ(target.X, target.Y, 0)
    return n


def roughin_views(doc, layout, takeoff, s):
    lvl = _level(doc)
    bbox = room_bbox(layout, 60.0)
    sc = int(s.get("plan_scale", 24))
    with _Tx(doc, "AEQ: rough-in plans"):
        v_eq = _plan(doc, "FS-101 EQUIPMENT PLAN", lvl, bbox, sc)
        v_el = _plan(doc, "FS-201 ELECTRICAL ROUGH-IN PLAN", lvl, bbox, sc)
        v_pl = _plan(doc, "FS-301 PLUMBING ROUGH-IN PLAN", lvl, bbox, sc)
        v_dm = _plan(doc, "FS-001 DEMOLITION PLAN", lvl, bbox, sc)
        try:
            v_dm.get_Parameter(BuiltInParameter.VIEW_PHASE).Set(_phase(doc, "New Construction").Id)
        except Exception:
            pass
        _halftone_equipment(doc, v_el)
        _halftone_equipment(doc, v_pl)
        doc.Regenerate()
        where = _equip_by_mark(doc)
        sched = {(r["key"], str(r.get("item") or "")): r for r in takeoff["schedule"]}
        # electrical callouts: one note per circuit, leader to the equipment
        seen = {}
        for c in takeoff["circuits"]:
            k = (c["key"], str(c.get("item") or ""))
            pts = where.get(k) or []
            i = seen.get(k, 0)
            seen[k] = i + 1
            tgt = pts[i] if i < len(pts) else (pts[0] if pts else None)
            if tgt is None:
                continue
            txt = "E-%s  %s V %s-PH  %s A\n%s-P %s A BKR  %s\n%s @ %d\" AFF" % (
                c["circuit"], c["volts"], c["phase"] or 1, c["amps"] if c["amps"] is not None else "VERIFY",
                c["poles"], c["breaker_a"] or "VERIFY", c["nema"] or "",
                "J-BOX / DIRECT" if c["conn"] == "direct" else "RECEPTACLE", c["height_aff"])
            _note(doc, v_el, tgt + XYZ(2.5, 2.0 + 1.4 * i, 0), tgt, txt)
        rd = C.read_json(os.path.join(s["repo_root"], "config", "program.json"))["rough_in_defaults"]
        synced = _synced(s)
        for (key, item), pts in where.items():
            r = sched.get((key, item))
            if not r or not r.get("plumb"):
                continue
            conns = [c for c in synced.get(key, {}).get("connectors", []) if "Piping" in c["domain"]]
            if conns:
                lines = ["%s %s\" @ %s\" AFF" % (c.get("system", "").replace("Domestic", "D"),
                                                  c.get("size_in", "?"), c["z_aff_in"]) for c in conns]
            else:
                lines = [r["plumb"], "SUPPLY @ %d\" AFF (STD - VERIFY)" % rd["cw_hw_supply_sink"]]
            _note(doc, v_pl, pts[0] + XYZ(2.5, -2.0, 0), pts[0], "P-%s\n%s" % (item, "\n".join(lines)))
    return [v_dm, v_eq, v_el, v_pl]


def _synced(s):
    p = os.path.join(s["repo_root"], "config", "families_synced.json")
    return C.read_json(p) if os.path.isfile(p) else {}


def interior_elevations(doc, layout, st, s):
    """One elevation marker at the room centre -> four interior elevations, cropped to the room."""
    lvl = _level(doc)
    x0, y0, x1, y1 = room_bbox(layout, 0.0)
    c = XYZ((x0 + x1) / 2.0 * FT, (y0 + y1) / 2.0 * FT, lvl.Elevation)
    plan = _first_plan(doc)
    vft = _vft(doc, ViewFamily.Elevation)
    names = ["NORTH", "WEST", "SOUTH", "EAST"]   # marker index order: 0=+X? verify on first run
    views = []
    with _Tx(doc, "AEQ: interior elevations"):
        mk = ElevationMarker.CreateElevationMarker(doc, vft.Id, c, int(s.get("elev_scale", 24)))
        for i in range(4):
            ev = mk.CreateElevation(doc, plan.Id, i)
            ev.Name = _unique_name(doc, type(ev), "FS-401 INTERIOR ELEVATION %s" % names[i])
            depth = ((x1 - x0) if i % 2 else (y1 - y0)) / 2.0 * FT + 0.5
            try:
                ev.get_Parameter(BuiltInParameter.VIEWER_BOUND_OFFSET_FAR).Set(depth)
            except Exception:
                pass
            views.append(ev)
    return views


def view_3d(doc, layout, st):
    lvl = _level(doc)
    x0, y0, x1, y1 = room_bbox(layout, 12.0)
    h = (st.get("ceiling_ft") or 10.0)
    with _Tx(doc, "AEQ: 3D view"):
        v = View3D.CreateIsometric(doc, _vft(doc, ViewFamily.ThreeDimensional).Id)
        v.Name = _unique_name(doc, View3D, "FS-501 3D KITCHEN")
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
    return v


def equipment_schedule(doc):
    """Equipment schedule (Mark / Family and Type / Count / Comments) for the food service category."""
    cat = None
    for inst in FilteredElementCollector(doc).OfClass(FamilyInstance):
        c = inst.get_Parameter(BuiltInParameter.ALL_MODEL_INSTANCE_COMMENTS)
        if c and c.AsString() and inst.Category and re.match(r"^[A-Z0-9_x]+$", c.AsString()):
            cat = inst.Category.Id
            break
    if cat is None:
        return None
    with _Tx(doc, "AEQ: equipment schedule"):
        vs = ViewSchedule.CreateSchedule(doc, cat)
        vs.Name = _unique_name(doc, ViewSchedule, "FS EQUIPMENT SCHEDULE")
        d = vs.Definition
        want = ["Mark", "Family and Type", "Count", "Comments"]
        fields = {}
        for sf in d.GetSchedulableFields():
            n = sf.GetName(doc)
            if n in want and n not in fields:
                fields[n] = d.AddField(sf)
        if "Mark" in fields:
            d.AddSortGroupField(ScheduleSortGroupField(fields["Mark"].FieldId))
        d.IsItemized = True
    return vs


def roughin_schedule_drafting(doc, takeoff, title="FS-202 ELECTRICAL ROUGH-IN SCHEDULE"):
    """Drafting-view table of circuits from takeoff.json (text + detail lines)."""
    cols = [("CKT", 0.45), ("ITEM", 0.45), ("EQUIPMENT", 2.2), ("V", 0.45), ("PH", 0.35), ("AMPS", 0.55),
            ("BKR", 0.65), ("CONN", 0.9), ("NEMA", 0.8), ("AFF", 0.5), ("RUN LF", 0.6)]
    rows = [[str(c["circuit"]), str(c.get("item") or ""), c["key"], str(c["volts"]), str(c["phase"] or 1),
             str(c["amps"] if c["amps"] is not None else "VERIFY"),
             "%s-P %sA" % (c["poles"], c["breaker_a"] or "?"), c["conn"] or "", c["nema"] or "",
             '%d"' % c["height_aff"], str(int(c["home_run_lf"]))] for c in takeoff["circuits"]]
    with _Tx(doc, "AEQ: rough-in schedule"):
        v = ViewDrafting.Create(doc, _vft(doc, ViewFamily.Drafting).Id)
        v.Name = _unique_name(doc, ViewDrafting, title)
        v.Scale = 1
        tn = FilteredElementCollector(doc).OfClass(TextNoteType).FirstElementId()
        rh = 0.022          # ft on sheet at 1:1 (~1/4")
        y = 0.0
        x_edges = [0.0]
        for _, w in cols:
            x_edges.append(x_edges[-1] + w / 12.0 * 1.0)
        for r_i, r in enumerate([[c for c, _ in cols]] + rows):
            for c_i, txt in enumerate(r):
                TextNote.Create(doc, v.Id, XYZ(x_edges[c_i] + 0.004, y - 0.004, 0), txt, TextNoteOptions(tn))
            y -= rh
            doc.Create.NewDetailCurve(v, Line.CreateBound(XYZ(0, y, 0), XYZ(x_edges[-1], y, 0)))
        for x in x_edges:
            doc.Create.NewDetailCurve(v, Line.CreateBound(XYZ(x, 0, 0), XYZ(x, y, 0)))
        doc.Create.NewDetailCurve(v, Line.CreateBound(XYZ(0, 0, 0), XYZ(x_edges[-1], 0, 0)))
    return v


# -------------------------------------------------------------- 6. sheets
def _titleblock(doc, name):
    tbs = list(FilteredElementCollector(doc).OfCategory(BuiltInCategory.OST_TitleBlocks)
               .WhereElementIsElementType())
    if not tbs:
        raise Exception("Template has no title block")
    if name:
        for t in tbs:
            if name.lower() in (t.Family.Name + " " + t.Name).lower():
                return t
    return tbs[0]


SHEETS = [("FS-001", "DEMOLITION PLAN", ["FS-001 DEMOLITION PLAN"]),
          ("FS-101", "FOODSERVICE EQUIPMENT PLAN", ["FS-101 EQUIPMENT PLAN", "FS EQUIPMENT SCHEDULE"]),
          ("FS-201", "ELECTRICAL ROUGH-IN PLAN", ["FS-201 ELECTRICAL ROUGH-IN PLAN"]),
          ("FS-202", "ELECTRICAL ROUGH-IN SCHEDULE", ["FS-202 ELECTRICAL ROUGH-IN SCHEDULE"]),
          ("FS-301", "PLUMBING ROUGH-IN PLAN", ["FS-301 PLUMBING ROUGH-IN PLAN"]),
          ("FS-401", "INTERIOR ELEVATIONS", ["FS-401 INTERIOR ELEVATION"]),
          ("FS-501", "3D VIEW", ["FS-501 3D KITCHEN"])]


def make_sheets(doc, s):
    tb = _titleblock(doc, s.get("titleblock_name"))
    views = {v.Name: v for v in FilteredElementCollector(doc).OfClass(View) if not v.IsTemplate}
    made = []
    with _Tx(doc, "AEQ: sheets"):
        existing = set(sh.SheetNumber for sh in FilteredElementCollector(doc).OfClass(ViewSheet))
        for num, name, prefixes in SHEETS:
            if num in existing:
                continue
            sh = ViewSheet.Create(doc, tb.Id)
            sh.SheetNumber, sh.Name = num, name
            doc.Regenerate()
            bb = None
            for e in FilteredElementCollector(doc, sh.Id).OfCategory(BuiltInCategory.OST_TitleBlocks):
                bb = e.get_BoundingBox(sh)
            cx = (bb.Min.X + bb.Max.X) / 2.0 if bb else 0.7
            cy = (bb.Min.Y + bb.Max.Y) / 2.0 if bb else 0.45
            hits = [v for n, v in sorted(views.items()) if any(n.startswith(p) for p in prefixes)]
            n = len(hits)
            for i, v in enumerate(hits):
                off = XYZ((i - (n - 1) / 2.0) * (bb.Max.X - bb.Min.X) / max(n, 1) * 0.9 if bb and n > 1 else 0,
                          0, 0)
                pt = XYZ(cx, cy, 0) + off
                if isinstance(v, ViewSchedule):
                    ScheduleSheetInstance.Create(doc, sh.Id, v.Id, XYZ(cx + 0.35, cy + 0.25, 0))
                elif Viewport.CanAddViewToSheet(doc, sh.Id, v.Id):
                    Viewport.Create(doc, sh.Id, v.Id, pt)
            made.append(sh)
    return made


# ------------------------------------------------------- 7. PDF + 3D render
def export_pdf_and_render(doc, s, st):
    out = C.store_out(s)
    sheets = sorted([sh for sh in FilteredElementCollector(doc).OfClass(ViewSheet)
                     if sh.SheetNumber.startswith("FS-")], key=lambda x: x.SheetNumber)
    ids = List[ElementId]([sh.Id for sh in sheets])
    opts = PDFExportOptions()
    opts.Combine = True
    base = "%s-FS-SET-%s" % (os.path.basename(st.get("drive_folder", st["store_id"])), s.get("rev", "R0"))
    opts.FileName = base
    doc.Export(out, ids, opts)
    renders = []
    for v in FilteredElementCollector(doc).OfClass(View3D):
        if v.Name.startswith("FS-501"):
            io = ImageExportOptions()
            io.ExportRange = ExportRange.SetOfViews
            io.SetViewsAndSheets(List[ElementId]([v.Id]))
            io.FilePath = os.path.join(out, base.replace("FS-SET", "3D"))
            io.ZoomType = ZoomFittype.FitToPage
            io.FitDirection = FitDirectionType.Horizontal
            io.PixelSize = 3600
            io.ImageResolution = ImageResolution.DPI_300
            io.ShadedViewsFileType = ImageFileType.PNG
            io.HLRandWFViewsFileType = ImageFileType.PNG
            doc.ExportImage(io)
            renders.append(io.FilePath + ".png")
    return os.path.join(out, base + ".pdf"), renders
