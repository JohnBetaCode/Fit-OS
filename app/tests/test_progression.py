from __future__ import annotations

from datetime import date, timedelta

import pytest

from fitos.training.progression import (
    CALIBRATION_NOTE,
    PlannedTarget,
    SessionPerf,
    SetPerf,
    detect_prs,
    e1rm,
    next_step,
    next_target,
    round_to_available,
)
from fitos.wa.parser import Target

CASA = {"dumbbell_kg": list(range(2, 25, 2))}
GYM = {"dumbbell_kg": [2.5 * i for i in range(1, 21)], "barbell_step_kg": 2.5, "machine_step_kg": 5,
       "cable_step_kg": 2.5, "plate_kg": [1.25, 2.5, 5, 10, 20]}
DB_CHEST = {"equipment_tag": "dumbbell", "load_kind": "per_hand", "primary_muscle": "chest", "mechanic": "compound"}
BB_BENCH = {"equipment_tag": "barbell", "load_kind": "total", "primary_muscle": "chest", "mechanic": "compound"}
BB_SQUAT = {"equipment_tag": "barbell", "load_kind": "total", "primary_muscle": "quads", "mechanic": "compound"}
MACHINE = {"equipment_tag": "machine", "load_kind": "total", "primary_muscle": "quads", "mechanic": "isolation"}
PUSHUP = {"equipment_tag": "bodyweight", "load_kind": "bodyweight", "primary_muscle": "chest"}
PLANK = {"equipment_tag": "bodyweight", "load_kind": "duration", "primary_muscle": "abs"}
ASSISTED = {"equipment_tag": "assisted_machine", "load_kind": "assisted", "primary_muscle": "lats"}


def sess(*sets, days_ago=0, eid=None):
    return SessionPerf(date(2026, 11, 15) - timedelta(days=days_ago), [SetPerf(*s) for s in sets], eid)


# ── e1RM ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("w", "r", "rir", "exp"),
    [(100, 10, None, 133.3), (100, 10, 2, 140.0), (100, 1, 0, 103.3), (100, 12, None, 140.0),
     (100, 11, 2, None), (100, 15, None, None), (None, 10, None, None), (0, 10, None, None), (100, None, None, None)],
)
def test_e1rm(w, r, rir, exp):
    assert e1rm(w, r, rir) == exp


# ── Redondeo y pasos ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("w", "lk", "tag", "loads", "direction", "exp"),
    [
        (21, "per_hand", "dumbbell", CASA, "nearest", 20),  # empate → abajo
        (21, "per_hand", "dumbbell", CASA, "up", 22),
        (21, "per_hand", "dumbbell", CASA, "down", 20),
        (30, "per_hand", "dumbbell", CASA, "nearest", 24),
        (1, "per_hand", "dumbbell", CASA, "down", 2),
        (22.5, "per_hand", "dumbbell", CASA, "nearest", 22),
        (61, "total", "barbell", GYM, "nearest", 60),
        (61, "total", "barbell", GYM, "up", 62.5),
        (62.5, "total", "barbell", GYM, "up", 62.5),
        (47, "total", "machine", GYM, "nearest", 45),
        (47.5, "total", "machine", GYM, "nearest", 45),  # empate → abajo
        (21, "total", "cable", GYM, "down", 20),
        (12, "bodyweight", "bodyweight", GYM, "nearest", None),
        (12, "duration", "bodyweight", GYM, "nearest", None),
        (None, "total", "barbell", GYM, "nearest", None),
    ],
)
def test_round_to_available(w, lk, tag, loads, direction, exp):
    assert round_to_available(w, lk, tag, loads, direction) == exp


@pytest.mark.parametrize(
    ("w", "tag", "lk", "loads", "upper", "exp"),
    [
        (60, "barbell", "total", GYM, True, 62.5),
        (100, "barbell", "total", GYM, False, 105),
        (20, "dumbbell", "per_hand", CASA, True, 22),
        (24, "dumbbell", "per_hand", CASA, True, 24),  # techo
        (22.5, "dumbbell", "per_hand", GYM, True, 25),
        (50, "machine", "total", GYM, False, 55),
        (20, "cable", "total", GYM, True, 22.5),
        (None, "barbell", "total", GYM, True, None),
    ],
)
def test_next_step(w, tag, lk, loads, upper, exp):
    assert next_step(w, tag, lk, loads, upper) == exp


# ── next_target ──────────────────────────────────────────────────────────────


def test_no_history_is_calibration():
    t = next_target([], 6, 10, 2, DB_CHEST, CASA)
    assert isinstance(t, PlannedTarget) and isinstance(t, Target)
    assert t.weight_kg is None and t.action == "calibrate"
    assert "elige un peso con el que hagas ~10 dejando 3 en reserva" in t.note
    assert t.note == CALIBRATION_NOTE
    assert (t.rep_min, t.rep_max, t.target_rir, t.load_kind) == (6, 10, 2, "per_hand")


def test_all_sets_at_rep_max_goes_up_one_step():
    t = next_target([sess((10, 20, 2), (10, 20, 2), (10, 20, 3))], 6, 10, 2, DB_CHEST, CASA)
    assert t.weight_kg == 22 and t.action == "increase"
    assert "22 kg" in t.note


def test_barbell_upper_and_lower_increments():
    assert next_target([sess((10, 60, None), (10, 60, None))], 6, 10, 2, BB_BENCH, GYM).weight_kg == 62.5
    assert next_target([sess((10, 100, 2), (10, 100, 2))], 6, 10, 2, BB_SQUAT, GYM).weight_kg == 105


def test_machine_next_step():
    assert next_target([sess((15, 50, 2), (15, 50, 2))], 10, 15, 2, MACHINE, GYM).weight_kg == 55


def test_rep_max_but_rir_below_target_holds():
    t = next_target([sess((10, 20, 0), (10, 20, 1))], 6, 10, 2, DB_CHEST, CASA)
    assert t.weight_kg == 20 and t.action == "hold"


def test_below_rep_max_holds_and_adds_reps():
    t = next_target([sess((10, 20, 2), (9, 20, 2), (8, 20, 2))], 6, 10, 2, DB_CHEST, CASA)
    assert t.weight_kg == 20 and t.action == "hold"
    assert "10, 9, 8" in t.note and "+1" not in t.note


def test_rir_autoregulation_increases_without_rep_max():
    t = next_target([sess((8, 20, 4), (8, 20, 5))], 6, 10, 2, DB_CHEST, CASA)
    assert t.weight_kg == 22 and t.action == "increase"
    assert "RIR" in t.note


def test_regression_after_two_sessions_below_rep_min():
    hist = [sess((5, 60, 0), (4, 60, 0), (4, 60, 0)), sess((5, 60, 0), (5, 60, 0), (6, 60, 0), days_ago=3)]
    t = next_target(hist, 6, 10, 2, BB_BENCH, GYM)
    assert t.action == "regress"
    assert 54 <= t.weight_kg <= 57.5  # −5 a −10 %


def test_one_bad_session_is_not_regression():
    hist = [sess((5, 60, 0), (4, 60, 0)), sess((8, 60, 2), (7, 60, 2), days_ago=3)]
    assert next_target(hist, 6, 10, 2, BB_BENCH, GYM).action == "hold"


def test_regression_with_dumbbells_drops_to_lower_pair():
    hist = [sess((4, 10, 0), (4, 10, 0)), sess((5, 10, 0), (4, 10, 0), days_ago=2)]
    assert next_target(hist, 6, 10, 2, DB_CHEST, CASA).weight_kg == 8


def test_load_ceiling_extends_then_flags():
    t = next_target([sess((10, 24, 2), (10, 24, 2))], 6, 10, 2, DB_CHEST, CASA)
    assert (t.weight_kg, t.rep_max, t.action, t.load_ceiling) == (24, 15, "extend", False)
    t = next_target([sess((15, 24, 2), (15, 24, 2))], 6, 10, 2, DB_CHEST, CASA)
    assert (t.weight_kg, t.rep_max, t.action, t.load_ceiling) == (24, 15, "ceiling", True)


def test_hold_rounds_to_todays_profile():
    # 22,5 kg en el gym → en casa no existe: el par más cercano (empate → abajo).
    t = next_target([sess((8, 22.5, 2), (7, 22.5, 2))], 6, 10, 2, DB_CHEST, CASA)
    assert t.weight_kg == 22


def test_bodyweight_progression():
    assert next_target([], 8, 15, 2, PUSHUP, {}).action == "bodyweight"
    t = next_target([sess((15, None, 2), (15, None, 2))], 8, 15, 2, PUSHUP, {})
    assert t.weight_kg is None and t.rep_max == 20 and t.action == "extend"


def test_duration_target():
    t = next_target([sess((None, None, None, 45))], 30, 60, 2, PLANK, {})
    assert t.weight_kg is None and t.action == "duration" and "45 s" in t.note


def test_assisted_increase_means_less_assistance():
    t = next_target([sess((10, 30, 2), (10, 30, 2))], 6, 10, 2, ASSISTED, GYM)
    assert t.weight_kg == 25 and t.action == "increase"


def test_target_rir_passthrough_and_none_default():
    t = next_target([sess((10, 20, None), (10, 20, None))], 6, 10, None, DB_CHEST, CASA)
    assert t.target_rir is None and t.weight_kg == 22


# ── PRs ──────────────────────────────────────────────────────────────────────


PRIOR = {"e1rm": 77.0, "weight": 60.0, "reps_at_weight": {60.0: 8, "55": 10}}


@pytest.mark.parametrize(
    ("sets", "flags"),
    [
        ([SetPerf(8, 60, 0)], []),
        ([SetPerf(9, 60, 0)], ["e1rm", "reps_at_weight"]),
        ([SetPerf(3, 62.5, 0)], ["weight"]),
        ([SetPerf(6, 62.5, 0)], ["weight"]),
        ([SetPerf(10, 58, 2)], ["e1rm"]),
        ([SetPerf(11, 55, None)], ["reps_at_weight"]),  # e1RM 75,2 < 77; la clave "55" se normaliza
        ([SetPerf(10, 50, 0), SetPerf(9, 60, 0)], ["e1rm", "reps_at_weight"]),
        ([SetPerf(None, 100, None)], []),
    ],
)
def test_detect_prs(sets, flags):
    assert detect_prs(sets, PRIOR) == flags


def test_first_time_has_no_prs():
    assert detect_prs([SetPerf(10, 40, 2)], {}) == []
    assert detect_prs([SetPerf(10, 40, 2)], None) == []
