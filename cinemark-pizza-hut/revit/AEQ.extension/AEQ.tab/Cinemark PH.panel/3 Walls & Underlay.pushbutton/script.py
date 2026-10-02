# -*- coding: utf-8 -*-
"""Link the survey DXF as an underlay and build existing / demo / new walls by phase."""
__title__ = "3 Walls &\nUnderlay"
from pyrevit import forms, script
from aeq_cinemark import config as C, build, ui

out = script.get_output()
s = C.load()
try:
    doc = ui.require_doc(__revit__)
    st = C.store(s)
    lay = C.layout(s)
    dxf = ui.dxf_path(s, st)
    if dxf:
        build.link_dxf(doc, dxf)
    r = build.build_walls(doc, lay, st)
    out.print_md("Walls created: **%d**" % r["created"])
    if r["type_mismatch_in"]:
        out.print_md("Closest wall type used for thicknesses (in): %s - add matching wall types to the template."
                     % sorted(set(r["type_mismatch_in"])))
except Exception as ex:
    forms.alert(str(ex), exitscript=True)
