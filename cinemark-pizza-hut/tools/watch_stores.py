#!/usr/bin/env python3
"""Background watcher for the Revit desktop: re-run a store whenever its DXF changes.

Runs silently at logon (pythonw, below-normal priority) via the scheduled task that
desktop/setup_desktop.ps1 registers. It never opens a window or touches the Revit,
AutoCAD, Inventor or Fusion sessions you are working in:

  1. Every poll_seconds it checks each store's Drive folder for its layout DXF
     (config/stores/<id>.json -> drive_folder + layout_dxf).
  2. When a DXF is new or changed AND has stopped changing for one more poll (AutoCAD
     finished writing), it runs tools/run_store.py at low priority and writes the
     outputs to "<store Drive folder>/_AEQ OUTPUT/" (quote PDF, INTERNAL xlsx, Gantt,
     layout/takeoff JSON, STATUS.txt).
  3. Optional (settings auto_revit = true): queues a background Revit build of the
     drawings in a SEPARATE Revit instance via `pyrevit run`, but only after the
     machine has been idle revit_idle_minutes, so it never competes with your work.

Settings live in %APPDATA%/pyRevit/aeq_cinemark.json (shared with the ribbon tools).
Log: %LOCALAPPDATA%/AEQ/watch_stores.log. Stop it: Task Scheduler > "AEQ Cinemark Watcher".

    pythonw tools/watch_stores.py            # normal (scheduled task)
    python  tools/watch_stores.py --once     # one pass, print what it would do / did
"""
import argparse
import ctypes
import datetime as dt
import json
import logging
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
IS_WIN = os.name == "nt"

APPDATA = os.environ.get("APPDATA", os.path.expanduser("~"))
LOCAL = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
SETTINGS = os.path.join(APPDATA, "pyRevit", "aeq_cinemark.json")
STATE_DIR = os.path.join(LOCAL, "AEQ")

DEFAULTS = {
    "drive_root": r"G:\My Drive\CINEMARK\PIZZA HUT",
    "python_exe": sys.executable,
    "rev": "R0",
    "poll_seconds": 60,
    "output_subfolder": "_AEQ OUTPUT",
    "work_dir": r"C:\AEQ\work",          # Revit models stay local, never on the Drive stream
    "auto_revit": False,                  # turn on after the first interactive Revit run checks out
    "revit_idle_minutes": 10,
    "revit_year": 2026,
    "pyrevit_exe": "pyrevit",
}


def load_settings():
    s = dict(DEFAULTS)
    if os.path.isfile(SETTINGS):
        with open(SETTINGS, encoding="utf-8") as f:
            s.update(json.load(f))
    return s


def load_state(path):
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, path)


def stores():
    d = os.path.join(ROOT, "config", "stores")
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".json"):
            with open(os.path.join(d, fn), encoding="utf-8") as f:
                yield fn[:-5], json.load(f)


def store_folder(s, st):
    return os.path.join(s["drive_root"], st.get("drive_folder", "").split("/")[-1])


def find_dxf(s, st):
    folder = store_folder(s, st)
    want = st.get("layout_dxf")
    if want and os.path.isfile(os.path.join(folder, want)):
        return os.path.join(folder, want)
    if os.path.isdir(folder):        # tolerate a renamed export: newest .dxf in the folder
        dx = [os.path.join(folder, f) for f in os.listdir(folder) if f.lower().endswith(".dxf")]
        if dx:
            return max(dx, key=os.path.getmtime)
    return None


def fingerprint(path):
    st = os.stat(path)
    return [int(st.st_mtime), st.st_size]


def decide(prev, fp_now):
    """Change detection with a settle step.
    prev: {"done": fp of last processed file, "seen": fp seen last poll}
    Returns (action, new_prev): action in {"run", "wait", "idle"}."""
    prev = dict(prev or {})
    if fp_now == prev.get("done"):
        return "idle", prev
    if fp_now == prev.get("seen"):          # unchanged since last poll -> AutoCAD finished
        return "run", prev
    prev["seen"] = fp_now
    return "wait", prev


def idle_minutes():
    """Minutes since the last keyboard/mouse input (Windows); 1e9 elsewhere."""
    if not IS_WIN:
        return 1e9

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(li)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li))
    return (ctypes.windll.kernel32.GetTickCount() - li.dwTime) / 60000.0


def low_priority_flags():
    if IS_WIN:
        # BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW
        return {"creationflags": 0x00004000 | 0x08000000}
    return {"preexec_fn": lambda: os.nice(10)}


def notify(title, text):
    """Quiet tray balloon (does not take focus). Best effort."""
    if not IS_WIN:
        return
    ps = ("Add-Type -AssemblyName System.Windows.Forms;"
          "$n=New-Object System.Windows.Forms.NotifyIcon;"
          "$n.Icon=[System.Drawing.SystemIcons]::Information;$n.Visible=$true;"
          "$n.ShowBalloonTip(8000,'%s','%s',[System.Windows.Forms.ToolTipIcon]::Info);"
          "Start-Sleep -s 9;$n.Dispose()") % (title.replace("'", ""), text.replace("'", ""))
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                         **low_priority_flags())
    except Exception:
        pass


def run_pipeline(s, store_id, st, dxf, log):
    out = os.path.join(store_folder(s, st), s["output_subfolder"])
    os.makedirs(out, exist_ok=True)
    args = [s["python_exe"], os.path.join(ROOT, "tools", "run_store.py"), store_id,
            "--dxf", dxf, "--rev", s.get("rev", "R0"), "--out", out]
    log.info("RUN %s", " ".join('"%s"' % a for a in args))
    p = subprocess.run(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       **low_priority_flags())
    text = p.stdout.decode("utf-8", "replace")
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(os.path.join(out, "STATUS.txt"), "w", encoding="utf-8") as f:
        f.write("%s  %s  %s\nsource: %s\n\n%s" % (
            stamp, store_id, "OK" if p.returncode == 0 else "FAILED (exit %d)" % p.returncode, dxf, text))
    log.info("%s exit %s\n%s", store_id, p.returncode, text)
    return p.returncode == 0, out, text


def queue_revit(s, store_id, out, log):
    """Write a job for revit/batch/build_store.py; launched later when the user is idle."""
    job = {"store_id": store_id, "outputs_dir": out, "work_dir": s["work_dir"],
           "queued": dt.datetime.now().isoformat()[:19]}
    path = os.path.join(STATE_DIR, "revit_jobs", store_id + ".json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(job, f, indent=1)
    log.info("queued Revit build %s", path)


def run_revit_jobs(s, log):
    jdir = os.path.join(STATE_DIR, "revit_jobs")
    if not os.path.isdir(jdir):
        return
    jobs = sorted(f for f in os.listdir(jdir) if f.endswith(".json"))
    if not jobs or idle_minutes() < s["revit_idle_minutes"]:
        return
    job = os.path.join(jdir, jobs[0])
    active = os.path.join(STATE_DIR, "revit_job_active.json")
    shutil.move(job, active)
    script = os.path.join(ROOT, "revit", "batch", "build_store.py")
    args = [s["pyrevit_exe"], "run", script, "--revit=%s" % s["revit_year"]]
    log.info("REVIT %s", " ".join(args))
    p = subprocess.run(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       **low_priority_flags())
    log.info("revit exit %s\n%s", p.returncode, p.stdout.decode("utf-8", "replace")[-4000:])
    notify("AEQ Revit build", "%s: drawings %s" % (jobs[0][:-5], "done" if p.returncode == 0 else "FAILED - see log"))


def poll_once(s, state, log):
    did = []
    for store_id, st in stores():
        dxf = find_dxf(s, st)
        if not dxf:
            continue
        action, new = decide(state.get(store_id), fingerprint(dxf))
        state[store_id] = new
        if action != "run":
            continue
        ok, out, text = run_pipeline(s, store_id, st, dxf, log)
        state[store_id]["done"] = fingerprint(dxf)
        state[store_id]["last_run"] = dt.datetime.now().isoformat()[:19]
        state[store_id]["ok"] = ok
        did.append((store_id, ok))
        total = next((l.split("$")[-1].split()[0] for l in text.splitlines() if l.strip().startswith("TOTAL")), "")
        notify("AEQ Cinemark %s" % store_id, ("quote updated: $%s" % total) if ok else "takeoff FAILED - see STATUS.txt")
        if ok and s.get("auto_revit"):
            queue_revit(s, store_id, out, log)
    if s.get("auto_revit"):
        run_revit_jobs(s, log)
    return did


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(STATE_DIR, exist_ok=True)
    logging.basicConfig(filename=os.path.join(STATE_DIR, "watch_stores.log"), level=logging.INFO,
                        format="%(asctime)s %(message)s")
    log = logging.getLogger("aeq")
    if a.once:
        log.addHandler(logging.StreamHandler(sys.stdout))
    state_path = os.path.join(STATE_DIR, "watch_state.json")
    log.info("watcher start, repo %s", ROOT)
    while True:
        s = load_settings()                       # re-read so Settings changes apply live
        state = load_state(state_path)
        try:
            poll_once(s, state, log)
        except Exception:
            log.exception("poll failed")
        save_state(state_path, state)
        if a.once:
            return 0
        time.sleep(max(15, int(s["poll_seconds"])))


if __name__ == "__main__":
    sys.exit(main())
