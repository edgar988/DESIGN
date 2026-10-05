# -*- coding: utf-8 -*-
"""Make the AEQ tags, connection symbols and the families from Edgar's DWG blocks (items with no KCL family), place
every item where the DWG has it, then sync KCL data back and re-run the takeoff."""
__title__ = "4 Place\nEquipment"
from pyrevit import forms, script
from aeq_cinemark import config as C, build, ui

out = script.get_output()
s = C.load()
try:
    doc = ui.require_doc(__revit__)
    placed, skipped = build.place_equipment(doc, C.layout(s), C.catalog(s), s)
    out.print_md("Placed **%d** items." % len(placed))
    for k, why in skipped:
        out.print_md("- skipped `%s`: %s" % (k, why))
    path, keys = build.sync_family_data(doc, C.catalog(s), s)
    out.print_md("Family data synced for %d types -> `%s`" % (len(keys), path))
    if forms.alert("Re-run takeoff & quote with the synced family data?", yes=True, no=True):
        ui.run_takeoff(s, out)
except Exception as ex:
    forms.alert(str(ex), exitscript=True)
