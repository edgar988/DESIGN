# -*- coding: utf-8 -*-
"""Create <store>_PH.rvt from the AEQ 11x17 foodservice template and open it."""
__title__ = "2 New\nModel"
from pyrevit import forms
from aeq_cinemark import config as C, build

s = C.load()
try:
    path = build.new_store_model(__revit__.Application, s)
    __revit__.OpenAndActivateDocument(path)
except Exception as ex:
    forms.alert(str(ex), exitscript=True)
