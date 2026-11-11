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
import re
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


def receptacle_for(nema):
    """The receptacle for an equipment plug and its rating: 5-15P -> ("5-20R", 20) (120 V cords go on 20 A
    circuits with a 5-20R, as priced); 6-30P -> ("6-30R", 30); 15-50P -> ("15-50R", 50). (None, None) when
    the plug is not known."""
    m = re.search(r"(L?\d{1,2})-(\d{2})P", nema or "")
    if not m:
        return None, None
    cfg, amps = m.group(1), int(m.group(2))
    if cfg == "5" and amps <= 20:
        return "5-20R", 20
    return "%s-%dR" % (cfg, amps), amps


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
        unnumbered = {}
        for q in layout["equipment"]:
            if q["status"] == "demo" or not q["key"]:
                continue
            if store.get("item_numbers") == "master" and not q.get("item"):
                unnumbered[q["key"]] = unnumbered.get(q["key"], 0) + 1     # not on the PH master list
                continue
            rows.append({"item": q.get("item"), "key": q["key"], "qty": int(q.get("stack") or 1),   # a stack: 2 ovens
                         "x": q["x"], "y": q["y"], "rotation": q["rotation"], "handle": q["handle"],
                         "roughin": q.get("roughin"), "provided_by": q.get("provided_by")})
        for r in rows:
            grouped.setdefault(r["key"], 0)
            grouped[r["key"]] += 1
        for k, n in sorted(unnumbered.items()):
            warn.append("Not priced, not on the master list: %s x%d (in scope? give it a master number)" % (k, n))
        rooms = layout["rooms"]
        room = None
        if rooms:                       # a kitchen can read as more than one space (TX-093: two)
            allp = [p for r in rooms for p in r["polygon"]]
            room = {"area_sf": sum(r["area_sf"] for r in rooms),
                    "perimeter_lf": sum(r["perimeter_lf"] for r in rooms),
                    "polygon": rooms[0]["polygon"] if len(rooms) == 1 else
                    [[min(p[0] for p in allp), min(p[1] for p in allp)], [max(p[0] for p in allp), min(p[1] for p in allp)],
                     [max(p[0] for p in allp), max(p[1] for p in allp)], [min(p[0] for p in allp), max(p[1] for p in allp)]]}
        pts = layout.get("points", {})
    else:
        if not isinstance(store.get("package"), list):
            raise SystemExit("Store has package=from_dxf but no --layout was given.")
        rows = [dict(p) for p in store["package"]]
        room, pts = None, {}
        warn.append("No layout: routing lengths use allowances, room quantities use store values.")

    # by item number (Juan, 2026-10-20): existing_items are already at the store - not supplied, moved and
    # reconnected like any existing item; omit_items are out of the scope altogether (no set, no utilities)
    omit = set(str(i) for i in store.get("omit_items") or [])
    on_site = set(str(i) for i in store.get("existing_items") or [])
    rows = [r for r in rows if str(r.get("item")) not in omit]
    for r in rows:
        if str(r.get("item")) in on_site:
            r["provided_by"] = "EXISTING"

    # with no panel / source drawn, they sit ~40 ft from the work (Edgar): measure from the middle of the
    # rough-ins, not the room, which can take in more than the kitchen (TX-093 reads two rooms)
    def _util(r):
        it = items.get(r["key"]) or {}
        return bool(it.get("elec") or it.get("plumb"))

    rough = [r.get("roughin") or [r["x"], r["y"]] for r in rows if _util(r) and (r.get("roughin") or "x" in r)]
    ref = ([sum(p[0] for p in rough) / len(rough), sum(p[1] for p in rough) / len(rough)] if rough
           else room and centroid(room["polygon"]))
    panel = pts.get("panel")
    # a measured panel distance (store electrical_service): to the connection on the panel's side, then across
    # (Edgar, NJ-187 alternate: the panel is 96 ft from the first, east, connection)
    es = store.get("electrical_service") or {}
    panel_ft = es.get("panel_distance_ft") or rt.get("panel_distance_ft", 25)
    el_ref = ref
    side = {"east": (0, max), "west": (0, min), "north": (1, max), "south": (1, min)}.get(es.get("panel_side"))
    if side:
        el_pts = [r.get("roughin") or [r["x"], r["y"]] for r in rows
                  if (items.get(r["key"]) or {}).get("elec") and (r.get("roughin") or "x" in r)]
        if el_pts:
            el_ref = side[1](el_pts, key=lambda p: p[side[0]])
    water = pts.get("water")
    waste = pts.get("waste") or water
    # more than one panel (GA-263 new area, Edgar 2026-11-10): each item's circuits come from its source; a
    # source with run_lf is a measured home-run length (the panel across the lobby, ~200 ft), one without is the
    # panel drawn in the layout. item_circuits: the breaker and wire Edgar gives for an item's circuits
    sources = es.get("sources") or {}
    item_src = es.get("item_sources") or {}
    item_ckt = es.get("item_circuits") or {}

    ceil_ft = store.get("ceiling_ft") or rt["default_ceiling_ft"]
    sched, circuits = [], []
    q_el = {"branch_lf": 0.0, "by_assembly": {}}
    q_pl = {"hw": 0, "cw": 0, "waste": 0, "indirect": 0, "supply_lf": 0.0}
    pl_by_item = {}                 # the same per item number, for a store that plumbs only some items
    sens = lat = 0.0
    conn_kw = 0.0
    counts = {"plug": 0, "plumbed": 0, "set": 0}
    by_source = {}                  # who provides it (item prefix): AEQ / EXISTING / OWNER -> install counts
    unverified = []
    ckt_at = {}                     # next circuit number at each source (one count when there is one panel)
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
        if it.get("category") != "accessory":          # faucets move with their sink
            src_counts = by_source.setdefault(r.get("provided_by") or "AEQ", {})
            src_counts[it.get("install", "set")] = src_counts.get(it.get("install", "set"), 0) + qty
        s, l_, hw = heat_of(it, qty, hl)
        if hw:
            warn.append("%s: %s" % (r["key"], hw))
        sens += s
        lat += l_
        row = {"item": r.get("item"), "provided_by": r.get("provided_by"),
               "key": r["key"], "qty": qty, "mfr": it["mfr"], "model": it["model"],
               "description": it["description"], "verified": it.get("verified", False),
               "flags": it.get("flags", []), "heat_sensible": round(s), "heat_latent": round(l_)}
        # runs go to the rough-in on the wall behind the item (dxf_extract), else to the item itself
        loc = r.get("roughin") or ([r["x"], r["y"]] if "x" in r else None)
        if e:
            kw = kw_of(e) * qty
            conn_kw += kw
            brk, poles = breaker_for(e)
            asm = connection_assembly(e)
            rcpt, rcpt_a = receptacle_for(e.get("nema")) if e.get("conn") != "direct" else (None, None)
            src_id = item_src.get(str(r.get("item"))) or es.get("default_source")
            src = sources.get(src_id) or {}
            given = item_ckt.get(str(r.get("item"))) or {}
            brk = given.get("breaker_a") or brk
            if rcpt_a:                  # the breaker matches the receptacle (never above it; 120 V: 20 A)
                if given.get("breaker_a", 0) > rcpt_a:
                    warn.append("Item %s: the %d A breaker given is above its %s receptacle; drawn and priced at "
                                "%d A (NEC 210.21(B)). Change the cord / receptacle to keep %d A." % (
                                    r.get("item"), given["breaker_a"], rcpt, rcpt_a, given["breaker_a"]))
                brk = rcpt_a if poles == 1 else min(brk or rcpt_a, rcpt_a)
            # up from the panel to the ceiling, over, and back down to the rough-in (Edgar)
            # refrigeration plugs at 78" A.F.F. on every job (Edgar); other cords under the counter
            aff_in = (rd["direct_connect_jbox"] if e.get("conn") == "direct"
                      else rd.get("receptacle_refrigeration", 78) if it.get("category") == "refrigeration"
                      else rd["receptacle_cord_under_counter"])
            aff_ft = aff_in / 12.0
            up_down = max(0.0, ceil_ft - rt.get("panel_top_ft", 6)) + max(0.0, ceil_ft - aff_ft)
            if src.get("run_lf"):           # a measured home run (Edgar: "about 200 ft")
                run = float(src["run_lf"])
            elif panel and loc:
                run = manhattan_ft(panel, loc) * rt["electrical_factor"] + up_down
            elif el_ref and loc:            # panel ~40 ft from the work, then across to the rough-in
                run = panel_ft + manhattan_ft(el_ref, loc) * rt["electrical_factor"] + up_down
            else:
                run = panel_ft + 15 + up_down
            run_asm = src.get("assembly") or "el_branch_lf"
            for _ in range(qty):
                n = ckt_at.get(src_id, 1)
                circuits.append({"circuit": n, "item": r.get("item"), "key": r["key"],
                                 "volts": e.get("volts"), "phase": e.get("phase"), "amps": e.get("amps"),
                                 "breaker_a": brk, "poles": poles, "conn": e.get("conn"),
                                 "nema": e.get("nema"), "receptacle": rcpt, "assembly": asm,
                                 "home_run_lf": round(run, 1),
                                 "height_aff": aff_in,
                                 "source": src_id, "source_label": src.get("short"), "wire": given.get("wire"),
                                 "neutral": bool(e.get("neutral")),
                                 "run_assembly": run_asm,
                                 "breaker_basis": ("given (Edgar)" if given.get("breaker_a") else
                                                   "125% nameplate - VERIFY MOCP on data plate")})
                ckt_at[src_id] = n + poles
                q_el["branch_lf"] += run
                q_el["by_assembly"][run_asm] = q_el["by_assembly"].get(run_asm, 0.0) + run
            row["elec"] = "%s/%s, %s A, %.1f kW, %s" % (e.get("volts"), e.get("phase"), e.get("amps"),
                                                      kw_of(e), e.get("conn"))
            if e.get("nema") == "VERIFY" or e.get("amps") is None:
                warn.append("%s: electrical data incomplete (NEMA/amps) - sync family or confirm spec." % r["key"])
        if p:
            mine = pl_by_item.setdefault(str(r.get("item")), {"hw": 0, "cw": 0, "waste": 0, "indirect": 0,
                                                              "supply_lf": 0.0})
            for k in ("hw", "cw"):
                if p.get(k):
                    q_pl[k] += qty
                    mine[k] += qty
            if p.get("waste"):
                k = "indirect" if p.get("indirect") else "waste"
                q_pl[k] += qty
                mine[k] += qty
            if (store.get("plumbing") or {}).get("supply_lf_each"):    # an existing supply right there (NJ)
                lf = float(store["plumbing"]["supply_lf_each"])
            elif water and loc:
                lf = manhattan_ft(water, loc) * rt["plumbing_factor"] + rt["plumbing_vertical_ft"]
            elif ref and loc:               # source taken ~40 ft away like the panel until one is drawn
                lf = (rt.get("water_distance_ft", 20) + manhattan_ft(ref, loc) * rt["plumbing_factor"]
                      + rt["plumbing_vertical_ft"])
            else:
                lf = 30.0
            q_pl["supply_lf"] += lf * qty
            mine["supply_lf"] += lf * qty
            row["plumb"] = ", ".join("%s %s" % (k.upper(), v) for k, v in p.items()
                                     if v and k in ("hw", "cw", "waste")) + (
                " (indirect)" if p.get("indirect") else "")
        sched.append(row)

    # ---- room / construction quantities ----------------------------------
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
        "equipment_by_source": by_source,
        "electrical": {"circuits": len(circuits), "branch_lf": round(q_el["branch_lf"]),
                       "branch_lf_by_assembly": {k: round(v) for k, v in q_el["by_assembly"].items()},
                       "connected_kw": round(conn_kw, 1),
                       "connected_amps_208_3ph": round(conn_kw * 1000 / (208 * math.sqrt(3)), 1)},
        "plumbing": {k: (round(v) if isinstance(v, float) else v) for k, v in q_pl.items()},
        "plumbing_by_item": {i: {k: (round(v) if isinstance(v, float) else v) for k, v in d.items()}
                             for i, d in pl_by_item.items()},
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
