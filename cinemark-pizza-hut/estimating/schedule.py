#!/usr/bin/env python3
"""Estimate -> construction timeline (working-day schedule + Gantt PNG).

Durations come from the estimate's labor hours by section and the crew size, so
the schedule moves when the scope moves. Pre-construction (permit, owner
equipment lead time) runs in calendar weeks; site work in working days (Mon-Fri,
or nights when the store's work_window says so).
"""
import datetime as dt
import math


def _nth(year, month, weekday, n):
    """nth weekday (0 = Mon) of a month; n = -1 for the last."""
    if n > 0:
        d = dt.date(year, month, 1)
        return d + dt.timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))
    d = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def holidays(year):
    """Days crews do not work: New Year, Memorial Day, July 4, Labor Day, Thanksgiving + Friday, Christmas
    Eve and Day (a weekend holiday moves to the nearest weekday)."""
    def obs(d):
        return d - dt.timedelta(days=1) if d.weekday() == 5 else d + dt.timedelta(days=1) if d.weekday() == 6 else d
    tg = _nth(year, 11, 3, 4)
    return {obs(dt.date(year, 1, 1)), _nth(year, 5, 0, -1), obs(dt.date(year, 7, 4)), _nth(year, 9, 0, 1),
            tg, tg + dt.timedelta(days=1), obs(dt.date(year, 12, 24)), obs(dt.date(year, 12, 25))}


def _working(d):
    return d.weekday() < 5 and d not in holidays(d.year)


def _wd_add(start, n):
    """Add n working days (Mon-Fri, holidays off) to a date."""
    d = start
    step = 0
    while step < n:
        d += dt.timedelta(days=1)
        if _working(d):
            step += 1
    return d


def _next_wd(d):
    while not _working(d):
        d += dt.timedelta(days=1)
    return d


def build(est, store, ntp=None):
    crew = est["labor"]["crew"]
    hpd = 10
    hrs = {k: v["hrs"] for k, v in est["sections"].items()}

    def days(sec, people=crew, minimum=1):
        return max(minimum, math.ceil(hrs.get(sec, 0) / (people * hpd)))

    sch = store.get("schedule", {})
    ntp = ntp or _next_wd(dt.date.today() + dt.timedelta(days=7))
    permit_wk = sch.get("permit_weeks", 3)
    lead_wk = sch.get("equipment_lead_weeks", 6)

    tasks = []

    def task(name, start, dur, kind="site", after=None):
        end = _wd_add(start, dur - 1) if kind != "cal" else start + dt.timedelta(days=dur - 1)
        t = {"name": name, "start": start, "end": end, "days": dur, "kind": kind}
        tasks.append(t)
        return t

    t_sub = task("Contract / NTP, submittals & equipment release", ntp, 5, "pre")
    t_perm = task("Permit drawings, plan review & permits", _wd_add(t_sub["end"], 1), permit_wk * 5, "pre")
    t_lead = task("Owner equipment procurement & delivery (by owner)", ntp, lead_wk * 5, "owner")
    start_site = _next_wd(_wd_add(t_perm["end"], 1))
    t_mob = task("Mobilize, protection & site verification", start_site, 1)
    t_demo = task("Demolition & make-safe", _wd_add(t_mob["end"], 1), days("DEMOLITION"))
    rough = max(days("ELECTRICAL", max(1, crew - 1)), days("PLUMBING", max(1, crew - 1)))
    t_rough = task("Electrical & plumbing rough-in (incl. underslab, floor sinks)",
                   _wd_add(t_demo["end"], 1), rough)
    t_insp = task("Rough-in inspections (AHJ)", _wd_add(t_rough["end"], 1), 2, "insp")
    t_close = task("Partitions, blocking, FRP & ceiling grid", _wd_add(t_insp["end"], 1),
                   max(2, math.ceil(days("CONSTRUCTION & FINISHES") * 0.6)))
    t_hvac = task("HVAC: mini-splits, line sets, ductwork, exhaust", t_close["start"],
                  days("HVAC", 2, 2))
    t_floor = task("Flooring & base, ceiling tile, trims",
                   _wd_add(max(t_close["end"], t_hvac["end"]), 1),
                   max(2, math.ceil(days("CONSTRUCTION & FINISHES") * 0.4)))
    eq_ready = max(_wd_add(t_floor["end"], 1), _next_wd(t_lead["end"] + dt.timedelta(days=1)))
    t_set = task("Equipment set, final connections & lighting trim", eq_ready,
                 days("EQUIPMENT SET & START-UP", crew, 2))
    t_start = task("Start-up, commissioning & HVAC balance (Hobart start-up by Hobart)",
                   _wd_add(t_set["end"], 1), 1)
    t_final = task("Final inspections & health department", _wd_add(t_start["end"], 1), 3, "insp")
    t_punch = task("Punch list, clean & turnover", _wd_add(t_final["end"], 1), 1)

    site_wd = sum(1 for i in range((t_punch["end"] - start_site).days + 1)
                  if _working(start_site + dt.timedelta(days=i)))
    return {"ntp": ntp, "site_start": start_site, "turnover": t_punch["end"], "tasks": tasks,
            "site_working_days": site_wd,
            "calendar_weeks": round(((t_punch["end"] - ntp).days + 1) / 7.0, 1),
            "note": "Equipment set waits on owner delivery (%d wk lead assumed)." % lead_wk}


def gantt_png(sched, path, title=""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    colors = {"pre": "#8A8F98", "owner": "#C9CDD3", "site": "#C1121F", "insp": "#222222"}
    tasks = sched["tasks"]
    fig, ax = plt.subplots(figsize=(10, 0.36 * len(tasks) + 1.2), dpi=160)
    for i, t in enumerate(tasks):
        s = mdates.date2num(t["start"])
        e = mdates.date2num(t["end"] + dt.timedelta(days=1))
        ax.barh(i, e - s, left=s, height=0.55, color=colors.get(t["kind"], "#C1121F"),
                edgecolor="none")
    ax.set_yticks(range(len(tasks)))
    ax.set_yticklabels([t["name"] for t in tasks], fontsize=7.5)
    ax.invert_yaxis()
    ax.xaxis_date()
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(byweekday=0))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    plt.setp(ax.get_xticklabels(), fontsize=7, rotation=0)
    ax.grid(axis="x", color="#E5E5E5", linewidth=0.6)
    ax.set_axisbelow(True)
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    ax.tick_params(axis="y", length=0)
    if title:
        ax.set_title(title, fontsize=9, loc="left", color="#222222")
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(color=colors["pre"], label="Pre-construction"),
                       Patch(color=colors["owner"], label="By owner"),
                       Patch(color=colors["site"], label="Site work (SSG)"),
                       Patch(color=colors["insp"], label="Inspections")],
              fontsize=6.5, loc="upper right", frameon=False, ncol=2)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path
