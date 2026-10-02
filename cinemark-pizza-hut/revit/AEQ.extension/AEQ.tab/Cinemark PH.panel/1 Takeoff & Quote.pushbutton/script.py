# -*- coding: utf-8 -*-
"""DXF -> layout, takeoff, heat load, estimate, schedule, quote PDF + INTERNAL workbook (CPython)."""
__title__ = "1 Takeoff\n& Quote"
from pyrevit import script, forms
from aeq_cinemark import config as C, ui

out = script.get_output()
s = C.load()
try:
    ui.run_takeoff(s, out)
    out.print_md("Outputs in `%s`" % C.store_out(s))
except Exception as ex:
    forms.alert(str(ex), exitscript=True)
