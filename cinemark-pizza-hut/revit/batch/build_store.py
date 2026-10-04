# -*- coding: utf-8 -*-
"""Background Revit build for one store, run in a SEPARATE Revit instance:

    pyrevit run "C:\\AEQ\\design\\cinemark-pizza-hut\\revit\\batch\\build_store.py" --revit=2026

Launched by tools/watch_stores.py (auto_revit = true) when the machine has been idle,
so the Revit session you are working in is never touched. Reads the job the watcher
queued (%LOCALAPPDATA%\\AEQ\\revit_job_active.json), rebuilds <work_dir>\\<store>\\<store>_PH.rvt
from the template (previous model kept as *_prev.rvt), runs the same steps as ribbon
buttons 2-7, and copies the sheet-set PDF + 3D image into the store's Drive "_AEQ OUTPUT".

NOT YET RUN: validate buttons 2-7 interactively on GA-263 first, then enable auto_revit.
"""
import io
import json
import os
import shutil
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "AEQ.extension", "lib"))

from aeq_cinemark import config as C, build   # noqa: E402

LOCAL = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
JOB = os.path.join(LOCAL, "AEQ", "revit_job_active.json")


def _app():
    a = __revit__                       # noqa: F821  (pyRevit global)
    return getattr(a, "Application", a)


def main():
    job = C.read_json(JOB)
    s = C.load()
    s["store_id"] = job["store_id"]
    s["outputs_dir"] = job["work_dir"]          # model + exports stay local
    out_drive = job["outputs_dir"]
    st = C.store(s)
    lay = C.read_json(os.path.join(out_drive, "layout.json"))
    tk = C.read_json(os.path.join(out_drive, "takeoff.json"))
    app = _app()

    work = C.store_out(s)
    rvt = os.path.join(work, "%s_PH.rvt" % s["store_id"])
    if os.path.isfile(rvt):
        shutil.copy2(rvt, rvt.replace("_PH.rvt", "_PH_prev.rvt"))
        os.remove(rvt)
    build.new_store_model(app, s)
    doc = app.OpenDocumentFile(rvt)
    log = []
    try:
        dxf = os.path.join(os.path.dirname(out_drive), st.get("layout_dxf", ""))   # store Drive folder
        if os.path.isfile(dxf):
            build.link_dxf(doc, dxf)
        log.append(("walls", build.build_walls(doc, lay, st)))
        placed, skipped = build.place_equipment(doc, lay, C.catalog(s), s)
        log.append(("placed", len(placed)))
        log.append(("skipped", skipped))
        log.append(("synced", build.sync_family_data(doc, C.catalog(s), s)[1]))
        build.roughin_views(doc, lay, tk, s)
        build.interior_elevations(doc, lay, st, s)
        build.view_3d(doc, lay, st)
        build.equipment_schedule(doc)
        build.roughin_schedule_drafting(doc, tk)
        build.make_sheets(doc, s)
        pdf, imgs = build.export_pdf_and_render(doc, s, st)
        for f in [pdf] + imgs:
            if os.path.isfile(f):
                shutil.copy2(f, out_drive)
        log.append(("exported", [os.path.basename(f) for f in [pdf] + imgs]))
        doc.Save()
        status = "OK"
    except Exception:
        status = "FAILED"
        log.append(("error", traceback.format_exc()))
    finally:
        doc.Close(False)
    with io.open(os.path.join(out_drive, "REVIT_STATUS.txt"), "w", encoding="utf-8") as f:
        f.write(u"%s  %s\nmodel: %s\n\n%s" % (status, s["store_id"], rvt,
                                              u"\n".join(u"%s: %s" % (k, v) for k, v in log)))
    os.remove(JOB)


try:
    main()
except Exception:
    with io.open(os.path.join(LOCAL, "AEQ", "revit_build_error.txt"), "w", encoding="utf-8") as f:
        f.write(u"%s" % traceback.format_exc())
    if os.path.isfile(JOB):
        os.rename(JOB, JOB.replace(".json", "_failed.json"))
