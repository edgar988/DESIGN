"""Rough-in material list rules (tools/roughin_materials.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import roughin_materials as M  # noqa: E402


def test_receptacle_matches_the_plug():
    assert M.receptacle_for("5-15P") == ("5-20R", 20)      # 120 V circuits are 20 A, as priced
    assert M.receptacle_for("6-30P") == ("6-30R", 30)
    assert M.receptacle_for("15-50P") == ("15-50R", 50)
    assert M.receptacle_for("VERIFY") == (None, None)


def test_breaker_never_above_the_receptacle_and_wire_follows_it():
    acp = {"poles": 2, "breaker_a": 35, "conn": "cord", "nema": "6-30P", "volts": 208}
    brk, poles, rec, cond = M.circuit_plan(acp)
    assert (brk, poles, rec) == (30, 2, "6-30R")
    assert ("10", "BLACK", 1) in cond and ("10", "RED", 1) in cond and ("10", "GREEN", 1) in cond
    assert not any(c[1] == "WHITE" for c in cond)           # 6-30R has no neutral
    oven = {"poles": 3, "breaker_a": 45, "conn": "cord", "nema": "15-50P", "volts": 208}
    brk, _, rec, cond = M.circuit_plan(oven)
    assert (brk, rec) == (45, "15-50R")
    assert [c[0] for c in cond if c[1] != "GREEN"] == ["8", "8", "8"] and ("10", "GREEN", 1) in cond
    fridge = {"poles": 1, "breaker_a": 15, "conn": "cord", "nema": "5-15P", "volts": 115}
    brk, _, rec, cond = M.circuit_plan(fridge)
    assert (brk, rec) == (20, "5-20R") and ("12", "WHITE", 1) in cond


def test_counts_add_up_per_circuit():
    takeoff = {"circuits": [
        {"key": "A", "poles": 1, "breaker_a": 15, "conn": "cord", "nema": "5-15P", "volts": 115, "home_run_lf": 50},
        {"key": "A", "poles": 1, "breaker_a": 15, "conn": "cord", "nema": "5-15P", "volts": 115, "home_run_lf": 30},
        {"key": "HOBART_LXNR", "poles": 3, "breaker_a": 35, "conn": "direct", "nema": None, "volts": 208,
         "home_run_lf": 40}],
        "quantities": {"electrical": {}, "plumbing_by_item": {}}}
    items, notes, wire, runs = M.electrical(takeoff, {})
    assert runs == 3
    assert items[("BREAKERS", "Circuit breaker, 1-pole 20 A")] == 2
    assert items[("BREAKERS", "Circuit breaker, 3-pole 35 A")] == 1
    assert items[("DEVICES", "Receptacle, NEMA 5-20R, 20 A 125 V, GFCI, commercial grade")] == 2
    assert items[("CONDUIT", 'EMT 3/4", 10 ft lengths (LF)')] == 120
    # #12 black: two 120 V runs (50 + 30 ft) x 1.10 + 2 ft tails each end
    assert round(wire[("12", "BLACK")], 1) == round((50 * 1.1 + 4) + (30 * 1.1 + 4), 1)
    assert ("8", "BLUE") in wire                            # the 35 A 3-pole Hobart: #8
