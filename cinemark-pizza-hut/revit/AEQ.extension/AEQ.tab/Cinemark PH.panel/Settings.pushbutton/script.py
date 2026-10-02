# -*- coding: utf-8 -*-
"""Pick the store and the local paths (repo, Drive folder, families, template, python)."""
__title__ = "Settings"
import os
from pyrevit import forms
from aeq_cinemark import config as C

s = C.load()
stores = sorted(f[:-5] for f in os.listdir(os.path.join(s["repo_root"], "config", "stores")) if f.endswith(".json"))
pick = forms.SelectFromList.show(stores, title="Store", multiselect=False)
if pick:
    s["store_id"] = pick
for key, label in [("drive_root", "CINEMARK\\PIZZA HUT folder (Google Drive for Desktop)"),
                   ("families_dir", "CAD TEMPLATES folder (.rfa families)")]:
    if forms.alert("%s:\n%s\n\nChange it?" % (label, s[key]), yes=True, no=True):
        f = forms.pick_folder(title=label)
        if f:
            s[key] = f
if forms.alert("Template:\n%s\n\nChange it?" % s["template_rte"], yes=True, no=True):
    f = forms.pick_file(file_ext="rte")
    if f:
        s["template_rte"] = f
rev = forms.ask_for_string(default=s.get("rev", "R0"), prompt="Revision (R0, R1, ...)")
if rev:
    s["rev"] = rev
py = forms.ask_for_string(default=s["python_exe"], prompt="Python 3 with ezdxf/openpyxl/reportlab/matplotlib")
if py:
    s["python_exe"] = py
C.save(s)
forms.alert("Saved for %s" % s["store_id"])
