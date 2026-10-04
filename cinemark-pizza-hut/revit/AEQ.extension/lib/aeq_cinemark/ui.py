# -*- coding: utf-8 -*-
"""Shared helpers for the ribbon buttons."""
import os
import subprocess

from aeq_cinemark import config as C


def dxf_path(s, st):
    """The store DXF lives in the store's Drive folder (Edgar saves it there from AutoCAD)."""
    folder = st.get("drive_folder", "").split("/")[-1]
    cands = [os.path.join(s["drive_root"], folder, st.get("layout_dxf", "")),
             os.path.join(C.store_out(s), st.get("layout_dxf", ""))]
    for c in cands:
        if os.path.isfile(c):
            return c
    return None


def run_takeoff(s, output):
    """Run the CPython pipeline (tools/run_store.py) and stream its report into the pyRevit output."""
    st = C.store(s)
    args = [s["python_exe"], os.path.join(s["repo_root"], "tools", "run_store.py"), s["store_id"],
            "--rev", s.get("rev", "R0"), "--out", C.store_out(s)]
    dxf = dxf_path(s, st)
    if dxf:
        args += ["--dxf", dxf]
    else:
        output.print_md("**No DXF found** for %s - pricing from the store package list." % s["store_id"])
    p = subprocess.Popen(args, cwd=s["repo_root"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out, _ = p.communicate()
    text = out.decode("utf-8", "replace") if hasattr(out, "decode") else out
    output.print_code(text)
    if p.returncode != 0:
        raise Exception("run_store.py failed (exit %s) - see output above" % p.returncode)
    return text


def require_doc(revit):
    doc = revit.ActiveUIDocument.Document if revit.ActiveUIDocument else None
    if doc is None or doc.IsFamilyDocument:
        raise Exception("Open the store model (<store>_PH.rvt) first - button 2 creates it.")
    return doc
