# -*- coding: utf-8 -*-
"""Combined FS sheet-set PDF + 3600 px realistic 3D image into the store output folder."""
__title__ = "7 Export\nPDF & 3D"
from pyrevit import forms, script
from aeq_cinemark import config as C, build, ui

s = C.load()
try:
    doc = ui.require_doc(__revit__)
    pdf, imgs = build.export_pdf_and_render(doc, s, C.store(s))
    script.get_output().print_md("PDF: `%s`\n\n3D: %s" % (pdf, ", ".join("`%s`" % i for i in imgs)))
except Exception as ex:
    forms.alert(str(ex), exitscript=True)
