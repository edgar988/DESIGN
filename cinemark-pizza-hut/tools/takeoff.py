#!/usr/bin/env python3
"""layout.json + store + catalog -> takeoff.json (rough-in schedule, quantities, heat load).

The takeoff is the single source the estimate, the Revit rough-in tags and the
schedules all read, so every number on the drawings and in the quote ties out.

Usage:
    python tools/takeoff.py --store config/stores/GA-263.json [--layout stores/GA-263/layout.json] -o stores/GA-263/takeoff.json
Without --layout the store's explicit "package" list is used (no routing geometry;
lengths fall back to allowances).
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
STD_BREAKERS = [15, 20, 25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100, 110, 125]


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_catalog(path=None):
    path = path or os.path.join(ROOT, "config", "families.json")
    cat = load(path)
    sp = os.path.join(os.path.dirname(path), "families_synced.json")
    return overlay_synced(cat, load(sp) if os.path.isfile(sp) else None)


def overlay_synced(catalog, synced):
    """Fill gaps in unverified catalog entries from config/families_synced.json (written by the
    Revit button '4 Place Equipment' from the KCL family parameters). Verified manufacturer data
    always wins; synced values only fill nulls / VERIFY placeholders."""
    if not synced:
        return catalog
    items = catalog["items"]
    for key, sy in synced.items():
        it = items.get(key)
        if not it or it.get("verified") is True:
            continue
        e = it.get("elec")
        se = sy.get("elec") or {}
        if e is not None and se:
            for k in ("volts", "phase", "amps", "kw", "nema"):
                if e.get(k) in (None, "VERIFY") and se.get(k) not in (None, ""):
                    e[k] = se[k]
        pipes = [c for c in sy.get("connectors", []) if "Piping" in c.get("domain", "")]
        if pipes:
            it["connectors"] = pipes
        filled = (e is None or all(e.get(k) not in (None, "VERIFY") for k in ("volts", "amps")))
        if filled:
            it["verified"] = "family"
            it["source"] = "KCL family parameters %s/%s (synced %s)" % (
                sy.get("revit_family"), sy.get("revit_type"), sy.get("synced_at"))
    return catalog


def kw_of(elec):
    if not elec:
        return 0.0
    if elec.get("kw"):
        return float(elec["kw"])
    v, a, ph = elec.get("volts"), elec.get("amps"), elec.get("phase") or 1
    if v and a:
        return v * a * (math.sqrt(3) if ph == 3 else 1) / 1000.0
    return 0.0


def breaker_for(elec):
    """Branch breaker sized at the next standard rating >= 125% of nameplate amps.
    Returns (amps, poles) or (None, poles) when amps are unknown. The mfr's MOCP
    on the data plate governs - this is a takeoff placeholder, flagged as such."""
    v, a, ph = elec.get("volts") or 0, elec.get("amps"), elec.get("phase")
    poles = 3 if ph == 3 else (1 if v and v <= 120 else 2)
    if a is None:
        return None, poles
    need = a * 1.25
    return next((b for b in STD_BREAKERS if b >= need), STD_BREAKERS[-1]), poles


def connection_assembly(elec):
    if elec.get("conn") == "direct":
        return "el_direct_conn"
    v, ph, a = elec.get("volts") or 0, elec.get("phase"), elec.get("amps") or 0
    if ph == 3:
        return "el_rcpt_3ph"
    if v <= 120 and a <= 16:
        return "el_rcpt_5_20"
    return "el_rcpt_1ph_208"


def heat_of(it, qty, hl):
    h = it.get("heat")
    if not h:
        return 0.0, 0.0, None
    m = h.get("method")
    if m == "fixed":
        return h["sensible"] * qty, h["latent"] * qty, None
    if m == "duty":
        kw = h.get("kw") or kw_of(it.get("elec"))
        if not kw:
            return 0.0, 0.0, "no kW for heat load"
        tot = kw * hl["btu_per_kw"] * h["duty"] * qty
        lat = tot * h.get("latent_frac", 0.0)
        return tot - lat, lat, None
    if m == "refrig":
        w = h.get("input_w")
        if not w:
            e = it.get("elec") or {}
            if e.get("volts") and e.get("amps"):
                w = e["volts"] * e["amps"] * 0.85
            else:
                return 0.0, 0.0, "no input watts for refrigeration heat"
        return w / 1000.0 * hl["btu_per_kw"] * h.get("run", 0.75) * qty, 0.0, None
    return 0.0, 0.0, "unknown heat method"


def manhattan_ft(p, q):
    return (abs(p[0] - q[0]) + abs(p[1] - q[1])) / 12.0


def centroid(poly):
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    return [sum(xs) / len(xs), sum(ys) / len(ys)]


def build(store, catalog, program, layout=None):
    items = catalog["items"]
    hl = program["heat_load"]
    rt = program["routing"]
    rd = program["rough_in_defaults"]
    warn = []

    # ---- equipment list -------------------------------------------------
    rows = []
    if layout:
        grouped = {}
        for q in layout["equipment"]:
            if q["status"] == "demo" or not q["key"]:
                continue
            rows.append({"item": q.get("item"), "key": q["key"], "qty": 1,
                         "x": q["x"], "y": q["y"], "rotation": q["rotation"], "handle": q["handle"]})
        for r in rows:
            grouped.setdefault(r["key"], 0)
            grouped[r["key"]] += 1
        room = layout["rooms"][0] if layout["rooms"] else None
        pts = layout.get("points", {})
    else:
        if not isinstance(store.get("package"), list):
            raise SystemExit("Store has package=from_dxf but no --layout was given.")
        rows = [dict(p) for p in store["package"]]
        room, pts = None, {}
        warn.append("No layout: routing lengths use allowances, room quantities use store values.")

    ref = room and centroid(room["polygon"])
    panel = pts.get("panel")
    water = pts.get("water")
    waste = pts.get("waste") or water

    sched, circuits = [], []
    q_el = {"branch_lf": 0.0}
    q_pl = {"hw": 0, "cw": 0, "waste": 0, "indirect": 0, "supply_lf": 0.0}
    sens = lat = 0.0
    conn_kw = 0.0
    counts = {"plug": 0, "plumbed": 0, "set": 0}
    unverified = []
    ckt = 1
    for r in rows:
        it = items.get(r["key"])
        if it is None:
            warn.append("Catalog has no key %s" % r["key"])
            continue
        qty = r.get("qty", 1)
        e, p = it.get("elec"), it.get("plumb")
        if it.get("verified") is False or it.get("verified") is None:
            unverified.append(r["key"])
        counts[it.get("install", "set")] = counts.get(it.get("install", "set"), 0) + qty
        s, l_, hw = heat_of(it, qty, hl)
        if hw:
            warn.append("%s: %s" % (r["key"], hw))
        sens += s
        lat += l_
        row = {"item": r.get("item"), "key": r["key"], "qty": qty, "mfr": it["mfr"], "model": it["model"],
               "description": it["description"], "verified": it.get("verified", False),
               "flags": it.get("flags", []), "heat_sensible": round(s), "heat_latent": round(l_)}
        loc = [r["x"], r["y"]] if "x" in r else None
        if e:
            kw = kw_of(e) * qty
            conn_kw += kw
            brk, poles = breaker_for(e)
            asm = connection_assembly(e)
            if panel and loc:
                run = manhattan_ft(panel, loc) * rt["electrical_factor"] + rt["electrical_vertical_ft"]
            elif ref and loc:
                run = manhattan_ft(ref, loc) * rt["electrical_factor"] + rt["electrical_vertical_ft"] + 25
            else:
                run = 45.0
            for _ in range(qty):
                circuits.append({"circuit": ckt, "item": r.get("item"), "key": r["key"],
                                 "volts": e.get("volts"), "phase": e.get("phase"), "amps": e.get("amps"),
                                 "breaker_a": brk, "poles": poles, "conn": e.get("conn"),
                                 "nema": e.get("nema"), "assembly": asm, "home_run_lf": round(run, 1),
                                 "height_aff": rd["direct_connect_jbox"] if e.get("conn") == "direct"
                                 else rd["receptacle_cord_under_counter"],
                                 "breaker_basis": "125% nameplate - VERIFY MOCP on data plate"})
                ckt += poles
                q_el["branch_lf"] += run
            row["elec"] = "%s/%s, %s A, %.1f kW, %s" % (e.get("volts"), e.get("phase"), e.get("amps"),
                                                      kw_of(e), e.get("conn"))
            if e.get("nema") == "VERIFY" or e.get("amps") is None:
                warn.append("%s: electrical data incomplete (NEMA/amps) - sync family or confirm spec." % r["key"])
        if p:
            for k in ("hw", "cw"):
                if p.get(k):
                    q_pl[k] += qty
            if p.get("waste"):
                if p.get("indirect"):
                    q_pl["indirect"] += qty
                else:
                    q_pl["waste"] += qty
            if water and loc:
                q_pl["supply_lf"] += (manhattan_ft(water, loc) * rt["plumbing_factor"]
                                      + rt["plumbing_vertical_ft"]) * qty
            else:
                q_pl["supply_lf"] += 30.0 * qty
            row["plumb"] = ", ".join("%s %s" % (k.upper(), v) for k, v in p.items()
                                     if v and k in ("hw", "cw", "waste")) + (
                " (indirect)" if p.get("indirect") else "")
        sched.append(row)

    # ---- room / construction quantities ----------------------------------
    ceil_ft = store.get("ceiling_ft") or rt["default_ceiling_ft"]
    if room:
        area, perim = room["area_sf"], room["perimeter_lf"]
    else:
        area = store.get("room_sf", 309.0)
        perim = store.get("room_perimeter_lf", 2 * (24.75 + 11.4) + 6)
    walls = (layout or {}).get("walls", [])

    def wall_lf(status):
        return sum(math.hypot(w["end"][0] - w["start"][0], w["end"][1] - w["start"][1])
                   for w in walls if w["status"] == status) / 12.0

    fin, dm = store.get("finishes", {}), store.get("demo", {})

    def pick(v, auto):
        return auto if v in ("from_dxf", None) else (area if v == "room" else v)

    doors_new = sum(1 for d in (layout or {}).get("doors", []) if d["status"] == "new")
    demo_eq = sum(1 for q in (layout or {}).get("equipment", []) if q["status"] == "demo")
    quantities = {
        "room_sf": round(area, 1), "room_perimeter_lf": round(perim, 1), "ceiling_ft": ceil_ft,
        "wall_sf": round(perim * ceil_ft, 1),
        "new_partition_lf": round(pick(fin.get("new_partition_lf"), wall_lf("new")), 1),
        "demo_partition_lf": round(pick(dm.get("partition_lf"), wall_lf("demo")), 1),
        "doors_new": pick(fin.get("doors"), doors_new),
        "demo_equipment": pick(dm.get("equipment_count"), demo_eq),
        "demo_ceiling_sf": pick(dm.get("ceiling_sf"), area),
        "demo_flooring_sf": pick(dm.get("flooring_sf"), area),
        "demo_casework_lf": dm.get("casework_lf", 0) or 0,
        "make_safe": pick(dm.get("make_safe"), demo_eq),
        "equipment_counts": counts,
        "electrical": {"circuits": len(circuits), "branch_lf": round(q_el["branch_lf"]),
                       "connected_kw": round(conn_kw, 1),
                       "connected_amps_208_3ph": round(conn_kw * 1000 / (208 * math.sqrt(3)), 1)},
        "plumbing": {k: (round(v) if isinstance(v, float) else v) for k, v in q_pl.items()},
    }

    # ---- heat load (GA 263 M1 method) -----------------------------------
    staff = store.get("staff", hl["default_staff"])
    occ_s, occ_l = staff * hl["occupant_sensible"], staff * hl["occupant_latent"]
    add_s, add_l = sens + occ_s, lat + occ_l
    des_s, des_l = add_s * hl["design_margin"], add_l * hl["design_margin"]
    des_t = des_s + des_l
    tons_req = des_t / hl["btu_per_ton"]
    step = hl["mini_split_ton_step"]
    override = (store.get("hvac") or {}).get("minisplit_override_tons")
    units = math.ceil(round(tons_req * 0.97 / step, 6))  # nominal within 3% accepted (M1 §4)
    tons = override or units * step
    units = int(round(tons / step))
    heat = {"equipment_sensible": round(sens), "equipment_latent": round(lat),
            "occupants": staff, "occupant_sensible": occ_s, "occupant_latent": occ_l,
            "added_sensible": round(add_s), "added_latent": round(add_l), "added_total": round(add_s + add_l),
            "design_sensible": round(des_s), "design_latent": round(des_l), "design_total": round(des_t),
            "tons_required": round(tons_req, 2), "minisplit_units": units, "minisplit_tons": tons,
            "supply_cfm": round(des_s / (1.08 * hl["supply_dt_f"])),
            "shr": round(des_s / des_t, 2) if des_t else None,
            "connected_kw": round(conn_kw, 1)}

    return {"store_id": store["store_id"], "schedule": sched, "circuits": circuits,
            "quantities": quantities, "heat_load": heat,
            "unverified": sorted(set(unverified)), "warnings": warn,
            "layout_used": bool(layout)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--layout")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--catalog", default=os.path.join(ROOT, "config", "families.json"))
    ap.add_argument("--program", default=os.path.join(ROOT, "config", "program.json"))
    a = ap.parse_args(argv)
    cat = load_catalog(a.catalog)
    t = build(load(a.store), cat, load(a.program), load(a.layout) if a.layout else None)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(t, f, indent=1)
    h = t["heat_load"]
    print("%s: %d items, %d circuits, %.1f kW connected" % (
        t["store_id"], len(t["schedule"]), len(t["circuits"]), h["connected_kw"]))
    print("heat: added %d Btu/h, design %d Btu/h = %.2f tons -> %d x %.1f-ton mini-splits" % (
        h["added_total"], h["design_total"], h["tons_required"], h["minisplit_units"],
        h["minisplit_tons"] / max(h["minisplit_units"], 1)))
    for w in t["warnings"]:
        print("WARNING:", w)
    if t["unverified"]:
        print("UNVERIFIED catalog entries (sync from Revit before issuing):", ", ".join(t["unverified"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
