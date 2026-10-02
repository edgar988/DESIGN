# -*- coding: utf-8 -*-
"""Demo / equipment / electrical / plumbing plans with rough-in callouts, interior elevations, 3D, schedules."""
__title__ = "5 Views &\nRough-In"
from pyrevit import forms, script
from aeq_cinemark import config as C, build, ui

out = script.get_output()
s = C.load()
try:
    doc = ui.require_doc(__revit__)
    st, lay, tk = C.store(s), C.layout(s), C.takeoff(s)
    vs = build.roughin_views(doc, lay, tk, s)
    ev = build.interior_elevations(doc, lay, st, s)
    v3 = build.view_3d(doc, lay, st)
    build.equipment_schedule(doc)
    build.roughin_schedule_drafting(doc, tk)
    out.print_md("Created %d plans, %d elevations, 3D view, schedules." % (len(vs), len(ev)))
    if tk.get("unverified"):
        out.print_md("**Unverified equipment data** (callouts say VERIFY): %s" % ", ".join(tk["unverified"]))
except Exception as ex:
    forms.alert(str(ex), exitscript=True)
