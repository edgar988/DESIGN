# -*- coding: utf-8 -*-
"""FS-001 ... FS-501 sheets on the AEQ 11x17 title block with views placed."""
__title__ = "6 Sheets"
from pyrevit import forms, script
from aeq_cinemark import config as C, build, ui

s = C.load()
try:
    doc = ui.require_doc(__revit__)
    made = build.make_sheets(doc, s)
    script.get_output().print_md("Sheets: %s" % ", ".join(sh.SheetNumber for sh in made))
except Exception as ex:
    forms.alert(str(ex), exitscript=True)
