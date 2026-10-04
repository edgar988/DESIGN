# -*- coding: utf-8 -*-
"""Local settings for the AEQ Cinemark PH pyRevit tools (IronPython 2.7 / CPython 3 safe).

Stored per user at %APPDATA%\\pyRevit\\aeq_cinemark.json so the repo stays machine-independent.
"""
import io
import json
import os

SETTINGS_PATH = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "pyRevit", "aeq_cinemark.json")

HERE = os.path.dirname(os.path.abspath(__file__))
# lib/aeq_cinemark -> lib -> AEQ.extension -> revit -> cinemark-pizza-hut
DEFAULT_REPO = os.path.normpath(os.path.join(HERE, "..", "..", "..", ".."))

DEFAULTS = {
    "repo_root": DEFAULT_REPO,
    # Google Drive for Desktop path to CINEMARK/PIZZA HUT (G: on desktop-tlo1t13 per HELM)
    "drive_root": r"G:\My Drive\CINEMARK\PIZZA HUT",
    "families_dir": r"G:\My Drive\CINEMARK\PIZZA HUT\CAD TEMPLATES",
    "template_rte": r"G:\My Drive\CINEMARK\PIZZA HUT\CAD TEMPLATES\AEQ_FOODSERVICE_11X17_2026.rte",
    "outputs_dir": os.path.join(DEFAULT_REPO, "stores"),
    "python_exe": "python",
    "store_id": "GA-263",
    "titleblock_name": "",          # blank = the title block the template's QF sheets use (AEQ 11x17)
    "plan_scale": 48,               # 1/4" = 1'-0" (steps down only if the room overflows the sheet)
    "elev_scale": 48,
    "sheet_area_in": [0.5, 0.65, 13.4, 10.5],   # AEQ 11x17 drawing area (x0, y0, x1, y1), left of the title strip
    "note_text_type": "AEQ - Utility Tags",     # rough-in callout text style
    "rev": "R0",
}


def load():
    s = dict(DEFAULTS)
    if os.path.isfile(SETTINGS_PATH):
        with io.open(SETTINGS_PATH, encoding="utf-8") as f:
            s.update(json.load(f))
    return s


def save(s):
    d = os.path.dirname(SETTINGS_PATH)
    if not os.path.isdir(d):
        os.makedirs(d)
    with io.open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        f.write(json.dumps(s, indent=2, ensure_ascii=False))


def read_json(path):
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=1, ensure_ascii=False))


def store(s=None):
    s = s or load()
    return read_json(os.path.join(s["repo_root"], "config", "stores", s["store_id"] + ".json"))


def catalog(s=None):
    s = s or load()
    return read_json(os.path.join(s["repo_root"], "config", "families.json"))


def store_out(s=None):
    s = s or load()
    p = os.path.join(s["outputs_dir"], s["store_id"])
    if not os.path.isdir(p):
        os.makedirs(p)
    return p


def layout(s=None):
    p = os.path.join(store_out(s), "layout.json")
    if not os.path.isfile(p):
        raise IOError("layout.json not found in %s - run '4 Takeoff & Quote' (or tools/run_store.py) first." % store_out(s))
    return read_json(p)


def takeoff(s=None):
    p = os.path.join(store_out(s), "takeoff.json")
    if not os.path.isfile(p):
        raise IOError("takeoff.json not found in %s - run '4 Takeoff & Quote' first." % store_out(s))
    return read_json(p)
