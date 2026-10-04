import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from tools import watch_stores as w          # noqa: E402
import make_sample_dxf                       # noqa: E402


def test_decide_waits_for_file_to_settle():
    a, st = w.decide({}, [100, 5])
    assert a == "wait"                         # first sighting: AutoCAD may still be writing
    a, st = w.decide(st, [101, 9])
    assert a == "wait"                         # still changing
    a, st = w.decide(st, [101, 9])
    assert a == "run"                          # unchanged for one poll -> process
    st["done"] = [101, 9]
    a, st = w.decide(st, [101, 9])
    assert a == "idle"                         # already processed


def test_poll_runs_pipeline_into_drive_output(tmp_path):
    drive = tmp_path / "PIZZA HUT"
    folder = drive / "TX 093 MCALLEN HOLLYWOOD"
    folder.mkdir(parents=True)
    make_sample_dxf.build(str(folder / "TX 093 MCALLEN HOLLYWOOD.dxf"))
    s = dict(w.DEFAULTS, drive_root=str(drive), python_exe=sys.executable, auto_revit=False)

    class L:
        def info(self, *a): pass
    state = {}
    assert w.poll_once(s, state, L()) == []                 # first pass: settle
    did = w.poll_once(s, state, L())                        # second pass: run
    assert did == [("TX-093", True)]
    out = folder / "_AEQ OUTPUT"
    names = os.listdir(out)
    assert any(n.endswith("_INTERNAL.xlsx") for n in names)
    assert any(n.endswith("C1_R0_%s.pdf" % __import__("datetime").date.today().strftime("%m-%d-%y")) for n in names)
    assert "STATUS.txt" in names and open(out / "STATUS.txt").read().split()[3] == "OK"
    assert w.poll_once(s, state, L()) == []                 # unchanged -> nothing
