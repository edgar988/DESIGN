#!/usr/bin/env python3
"""takeoff.json -> priced estimate (internal) for one store.

Builds every quote line from config/cost_db.json assemblies, adds general
conditions, supervision and travel, applies store overrides and rounding, and
returns a structure consumed by build_quote.py (customer PDF), the INTERNAL
workbook and schedule.py.

INTERNAL cost, margin and travel-pad data never leave this module's internal
outputs (HELM hard rule). The customer PDF only receives section sells and
scope text.
"""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

SECTION_ORDER = ["GENERAL CONDITIONS", "DEMOLITION", "CONSTRUCTION & FINISHES", "ELECTRICAL",
                 "PLUMBING", "HVAC", "EQUIPMENT SET & START-UP", "TRAVEL & SUPERVISION"]


def _load(name):
    with open(os.path.join(ROOT, "config", name), encoding="utf-8") as f:
        return json.load(f)


def price_line(asm, qty, rates, note=""):
    hrs = asm["hrs"] * qty
    mat, sub, eqp = asm["mat"] * qty, asm["sub"] * qty, asm["eqp"] * qty
    sell = (hrs * rates["labor_billed_hr"] + mat * (1 + rates["material_markup"])
            + sub * (1 + rates["sub_markup"]) + eqp * (1 + rates["equipment_markup"]))
    cost = hrs * rates["labor_true_cost_hr"] + mat + sub + eqp
    return {"qty": qty, "unit": asm["unit"], "desc": asm["desc"], "section": asm["section"],
            "hrs": round(hrs, 2), "mat": round(mat, 2), "sub": round(sub, 2), "eqp": round(eqp, 2),
            "cost": round(cost, 2), "sell": round(sell, 2), "basis": asm["basis"], "note": note}


def build_lines(takeoff, store, cost_db, rates):
    A = cost_db["assemblies"]
    q = takeoff["quantities"]
    el, pl = q["electrical"], q["plumbing"]
    hv = store.get("hvac", {})
    fin = store.get("finishes", {})
    plm = store.get("plumbing", {})
    es = store.get("electrical_service", {})
    scope = store.get("scope", {}) or {}          # Edgar's construction narrative for the store
    heat = takeoff["heat_load"]
    L = []

    def add(key, qty, note="", **override):
        if qty and qty > 0:
            ln = price_line(dict(A[key], **override), qty, rates, note)
            ln["assembly"] = key
            L.append(ln)

    # DEMOLITION
    lump = scope.get("demo_lump_sum")
    if lump:                                      # Edgar's figure, dumpsters included: a sell price
        ln = price_line(A["demo_lump_sum"], 1, rates, scope.get("demo_note", ""))
        ln.update({"sub": round(lump / (1 + rates["sub_markup"]), 2), "sell": float(lump), "assembly": "demo_lump_sum"})
        ln["cost"] = ln["sub"]
        L.append(ln)
    else:
        add("demo_equipment", q["demo_equipment"])
        add("demo_partition", q["demo_partition_lf"])
        add("demo_ceiling", q["demo_ceiling_sf"])
        add("demo_flooring", q["demo_flooring_sf"] if fin.get("floor") else 0)
        add("demo_casework", q["demo_casework_lf"])
        add("demo_make_safe", q["make_safe"])
    counters = scope.get("counters") or {}
    add("demo_counter_front", counters.get("front_lf", 0))
    add("demo_counter_back", counters.get("back_lf", 0))

    # CONSTRUCTION & FINISHES
    add("con_partition", q["new_partition_lf"])
    wall_hung = sum(r["qty"] for r in takeoff["schedule"] if r["key"] in (
        "ADVANCE_7PS65", "TABCO_FC3", "TABCO_WS12108"))
    add("con_blocking", wall_hung * 8, "8 LF per wall-hung item")
    if fin.get("frp"):
        add("con_frp", round(q["wall_sf"] * 0.85), "wall SF less openings (85%)")
    if fin.get("ceiling") == "act_washable":
        add("con_act_washable", q["room_sf"])
    if fin.get("floor") == "quarry":
        add("con_floor_quarry", q["room_sf"])
        add("con_cove_base", q["room_perimeter_lf"])
    elif fin.get("floor") == "epoxy":
        add("con_floor_epoxy", q["room_sf"])
    add("con_door", q["doors_new"])
    if fin.get("patch_paint", True):
        add("con_patch_paint", round(q["wall_sf"] * 0.15), "tie-ins and non-FRP surfaces")
    wall = scope.get("new_wall") or {}
    if wall.get("lf"):                            # TX / NJ: wall across the old stand front
        h = wall.get("height_ft") or q["ceiling_ft"]
        add("con_partition", wall["lf"], "new wall, %s ft high" % h)
        add("con_wall_finish", round(wall["lf"] * h * 2), "both sides")
        add("con_cased_opening", wall.get("cased_openings", 0), "staff pass-through each side")

    # ELECTRICAL
    if es.get("new_subpanel", True):
        add("el_subpanel", 1)
        add("el_feeder_lf", es.get("feeder_lf", 60))
    add("el_branch_lf", el["branch_lf"], "home runs from takeoff routing")
    brk = {"1": 0, "2": 0, "3": 0}
    asm_ct = {}
    for c in takeoff["circuits"]:
        brk[str(c["poles"])] += 1
        asm_ct[c["assembly"]] = asm_ct.get(c["assembly"], 0) + 1
    add("el_brk_1p20", brk["1"])
    add("el_brk_2p", brk["2"])
    add("el_brk_3p", brk["3"])
    for k, n in sorted(asm_ct.items()):
        add(k, n)
    if fin.get("lighting", True):
        add("el_lighting_sf", q["room_sf"])
    if hv.get("none"):
        n_hvac_ckts = 0
    elif hv.get("system_cost"):                   # one condensing unit + any exhaust fan
        n_hvac_ckts = 1 + (1 if hv.get("exhaust_fan") else 0)
        add("el_branch_lf", hv.get("condenser_circuit_lf", 0), "new circuit to the condensing unit")
    else:
        n_hvac_ckts = heat["minisplit_units"] + (1 if hv.get("exhaust_fan") else 0)
    add("el_hvac_circuit", n_hvac_ckts)

    # PLUMBING
    add("pl_supply_conn", pl["hw"] + pl["cw"])
    add("pl_supply_lf", pl["supply_lf"])
    add("pl_waste_conn", pl["waste"])
    add("pl_indirect", pl["indirect"])
    add("pl_floor_sink", plm.get("floor_sinks", 0))
    add("pl_underslab_lf", plm.get("underslab_lf", 0))
    add("pl_grease_interceptor", 1 if plm.get("grease_interceptor") else 0)
    hand_sinks = sum(r["qty"] for r in takeoff["schedule"] if r["key"] == "ADVANCE_7PS65")
    add("pl_mixing_valve", hand_sinks)
    add("pl_backflow", 1 if any(r["key"] == "HOBART_LXNR" for r in takeoff["schedule"]) else 0)
    add("pl_water_heater", 1 if plm.get("new_water_heater") else 0)

    # HVAC
    if hv.get("system_cost"):                     # Edgar's system (GA: 5 t, 2 x 2.5 t heads, $7,200 cost)
        heads = hv.get("heads", 2)
        add("hv_split_system", 1, "%s tons, %d indoor heads, marked up as material (design %s Btu/h)" % (
            hv.get("tons"), heads, format(heat["design_total"], ",")), mat=hv["system_cost"])
        add("hv_split_install", heads)
        add("hv_condenser_set", 1)
        add("hv_lineset_lf", hv.get("lineset_lf_each", 0) * heads, "%d line sets x %s ft" % (heads, hv.get("lineset_lf_each")))
        add("hv_condensate", heads, "new drain per head")
        add("hv_crane", 1 if hv.get("crane") else 0)
        add("hv_roof_pen", 0 if hv.get("penetration_existing") else hv.get("roof_penetrations", 0))
        add("hv_exhaust_fan", 1 if hv.get("exhaust_fan") else 0)
    elif not hv.get("none"):
        units = heat["minisplit_units"]
        add("hv_minisplit_2_5t", units, "%.2f tons required (design %s Btu/h)" % (
            heat["tons_required"], format(heat["design_total"], ",")))
        add("hv_duct_diffuser", units * 3, "2 supply + 1 filtered return per unit")
        add("hv_lineset_lf", hv.get("lineset_extra_lf", 0) * max(units, 0))
        add("hv_condensate", units)
        add("hv_exhaust_fan", 1 if hv.get("exhaust_fan") else 0)
        add("hv_roof_pen", hv.get("roof_penetrations", 0))

    # EQUIPMENT SET - by who provides each item (item prefix): AEQ / X existing / PH owner
    src = q.get("equipment_by_source")
    if src:
        owner, existing = src.get("OWNER", {}), src.get("EXISTING", {})
        n_owner = sum(owner.values())
        add("eq_receive_nashville", n_owner, "received and held at AEQ Nashville until the site is ready")
        if n_owner:
            miles = (store.get("travel") or {}).get("one_way_miles", 0)
            add("eq_transport", 1, "Nashville to site, %d mi each way" % miles,
                hrs=round(miles / 55.0 * 2, 1), mat=round(miles * 2 * rates["truck_fuel_per_trip_mile"], 2))
        add("eq_relocate", sum(existing.values()), "existing items moved to their new positions")
        # existing items are reset by the relocate line; only the plumbed / hard-wired ones also take the
        # connect line, to their new utilities
        for kind, key in (("plug", "eq_set_plug"), ("plumbed", "eq_set_plumbed"), ("set", "eq_set_nonutility")):
            n = sum(d.get(kind, 0) for s, d in src.items() if s != "EXISTING")
            n_ex = existing.get(kind, 0) if kind == "plumbed" else 0
            add(key, n + n_ex, "incl. %d existing reconnected to new utilities" % n_ex if n_ex else "")
    else:
        cnt = q["equipment_counts"]
        add("eq_receive_store", 1)
        add("eq_set_plug", cnt.get("plug", 0))
        add("eq_set_plumbed", cnt.get("plumbed", 0))
        add("eq_set_nonutility", cnt.get("set", 0))

    # GENERAL CONDITIONS
    add("gc_mobilize", 2, "rough-in trip + set/finish trip")
    add("gc_protection", 1)
    if not lump:                                  # a demo lump sum includes the dumpsters
        add("gc_dumpster", 1 + (1 if q["room_sf"] > 400 or q["demo_partition_lf"] > 20 else 0))
    add("gc_permit_allow", 1)
    add("gc_final_clean", q["room_sf"])
    return L


def travel_and_supervision(lines, store, rates):
    field_hrs = sum(l["hrs"] for l in lines)
    crew = store.get("crew_size") or rates["crew_size_default"]
    hpd = rates["hours_per_day"]
    site_days = max(4, math.ceil(field_hrs / (crew * hpd)))
    tr = store.get("travel", {})
    miles = tr.get("one_way_miles", 0)
    rooms = math.ceil(crew / rates.get("crew_per_room", 1))
    weeks = math.ceil(site_days / 5.0)
    out = []
    pm_hrs = round(field_hrs * 0.08, 1)
    out.append({"desc": "Project management, coordination, submittals and inspections (8% of field labor)",
                "qty": pm_hrs, "unit": "hr", "hrs": pm_hrs, "mat": 0, "sub": 0, "eqp": 0,
                "cost": round(pm_hrs * rates["labor_true_cost_hr"], 2),
                "sell": round(pm_hrs * rates["labor_billed_hr"], 2), "basis": "AEQ est - review"})
    if tr.get("mode") == "fly":
        # rotate home every 2 weeks; crew stays over the weekend in between
        trips = max(2, math.ceil(weeks / 2.0))
        cal_days = site_days + 2 * (weeks - 1)
        nights = cal_days + trips              # arrive the evening before each rotation
        travel_days = trips * 2
        exp = (crew * trips * rates["airfare_per_person_rt"]
               + (cal_days + travel_days) * rates["rental_car_per_day"] + 1200.0)
        thrs = crew * travel_days * 6
        perdiem_days = cal_days + travel_days
        note = "fly: %d rotations x %d crew airfare, rental vehicle, tools/material freight $1,200" % (trips, crew)
    elif miles > 150:
        weekly = miles <= rates.get("drive_home_weekends_max_mi", 450)
        trips = max(2, weeks) if weekly else 2
        drive_hrs = miles / 55.0
        thrs = round(crew * trips * 2 * drive_hrs, 1)
        travel_days = 0 if weekly else trips * 2
        nights = (site_days - weeks + trips) if weekly else site_days + 2 * (weeks - 1) + trips
        perdiem_days = site_days if weekly else site_days + 2 * (weeks - 1) + travel_days
        exp = miles * 2 * trips * rates["truck_fuel_per_trip_mile"]
        note = "drive: %d mi one way, %d round trips, %s" % (
            miles, trips, "home weekends" if weekly else "stay over weekends")
    else:
        trips, travel_days, nights, perdiem_days = weeks, 0, 0, 0
        thrs = round(crew * site_days * 2 * miles / 45.0, 1) if miles else 0
        exp = miles * 2 * site_days * rates["truck_fuel_per_trip_mile"]
        note = "local: daily commute %d mi" % miles
    lodging = rooms * nights * rates["lodging_per_room_night"]
    perdiem = crew * perdiem_days * rates["per_diem_per_person_day"]
    exp_total = exp + lodging + perdiem
    out.append({"desc": "Travel labor (crew of %d)" % crew, "qty": thrs, "unit": "hr", "hrs": thrs,
                "mat": 0, "sub": 0, "eqp": 0, "cost": round(thrs * rates["labor_true_cost_hr"], 2),
                "sell": round(thrs * rates["labor_billed_hr"], 2), "basis": note})
    out.append({"desc": "Lodging, per diem and transportation", "qty": 1, "unit": "LS", "hrs": 0,
                "mat": round(exp_total, 2), "sub": 0, "eqp": 0, "cost": round(exp_total, 2),
                "sell": round(exp_total * (1 + rates["travel_markup"]), 2),
                "basis": "lodging %d room-nights, per diem %d man-days, %s" % (
                    rooms * nights, crew * perdiem_days, note)})
    for o in out:
        o["section"] = "TRAVEL & SUPERVISION"
        o["note"] = ""
    meta = {"field_hrs": round(field_hrs, 1), "crew": crew, "site_days": site_days,
            "travel_days": travel_days, "trips": trips, "nights": nights, "rooms": rooms}
    return out, meta


def estimate(takeoff, store, program=None, cost_db=None):
    program = program or _load("program.json")
    cost_db = cost_db or _load("cost_db.json")
    rates = dict(program["rates"])
    if store.get("schedule", {}).get("work_window") in ("night", "after_hours", "weekend"):
        rates["labor_billed_hr"] *= 1 + rates["after_hours_premium"]   # always bill after-hours
    lines = build_lines(takeoff, store, cost_db, rates)
    tlines, meta = travel_and_supervision(lines, store, rates)
    lines += tlines

    sections = {}
    for l in lines:
        s = sections.setdefault(l["section"], {"cost": 0.0, "sell": 0.0, "hrs": 0.0, "lines": []})
        s["cost"] += l["cost"]
        s["sell"] += l["sell"]
        s["hrs"] += l["hrs"]
        s["lines"].append(l)
    sub_sell = sum(s["sell"] for s in sections.values())
    sub_cost = sum(s["cost"] for s in sections.values())
    cont = sub_sell * rates["contingency_pct"]

    # overrides: Edgar's numbers win, by section, then total
    ov = store.get("overrides", {}) or {}
    for sec, val in (ov.get("section_sell") or {}).items():
        if sec in sections:
            sections[sec]["sell_override"] = float(val)
    sec_sell = {k: v.get("sell_override", v["sell"]) for k, v in sections.items()}
    raw_total = sum(sec_sell.values()) + cont
    round_to = ov.get("round_to", 100)
    total = float(ov["total_sell"]) if ov.get("total_sell") else math.ceil(raw_total / round_to) * round_to
    # distribute contingency + rounding pro-rata into sections so the customer
    # sees clean section numbers that add to the total
    scale = total / sum(sec_sell.values()) if sec_sell else 1
    ordered = [s for s in SECTION_ORDER if s in sec_sell]
    cust = {}
    acc = 0
    for s in ordered[:-1]:
        cust[s] = round(sec_sell[s] * scale / 50.0) * 50
        acc += cust[s]
    if ordered:
        cust[ordered[-1]] = round(total - acc, 2)

    margin = (total - sub_cost) / total if total else 0
    return {"store_id": store["store_id"], "lines": lines, "sections": sections,
            "section_order": ordered, "customer_sections": cust,
            "subtotal_sell": round(sub_sell, 2), "contingency": round(cont, 2),
            "total": round(total, 2), "true_cost": round(sub_cost, 2), "margin_pct": round(margin * 100, 1),
            "labor": meta, "rates_used": rates,
            "markup_check": "OK" if margin >= 0.25 else "LOW MARGIN - review before issue"}
