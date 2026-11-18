from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace as NS

import pytest
from factories import add_sets, log_session, make_catalog, make_profile

from fitos.core.models import WorkoutSet
from fitos.training.metrics import (
    consistency,
    e1rm_series,
    recent_prs,
    session_summary,
    tonnage,
    weekly_hard_sets_by_muscle,
)

MON = date(2026, 11, 9)


@pytest.fixture
def cat(db):
    return make_catalog(db)


def test_tonnage_plain_objects():
    sets = [NS(reps=10, weight_kg=20, load_kind="per_hand"), NS(reps=5, weight_kg=100, load_kind="total"),
            NS(reps=12, weight_kg=None, load_kind="bodyweight"), NS(reps=10, weight_kg=50, is_warmup=True)]
    assert tonnage(sets) == 400 + 500
    assert tonnage(sets[:1], load_kind="total") == 200


def test_tonnage_workout_sets(db, cat):
    ws = log_session(db, MON, "db_bench", [(10, 20, 2), (8, 20, 1)], warmups=1)
    add_sets(db, ws, "bb_squat", [(5, 100, 2)])
    assert tonnage(ws.sets) == (10 + 8) * 20 * 2 + 500


def test_weekly_hard_sets_by_muscle(db, cat):
    log_session(db, MON, "db_bench", [(10, 20, 2), (10, 20, None), (10, 20, 5)], warmups=2)  # RIR 5 no cuenta
    log_session(db, MON + timedelta(days=2), "db_row", [(10, 20, 2)] * 3)
    log_session(db, MON + timedelta(days=7), "db_bench", [(10, 20, 2)] * 5)  # semana siguiente
    log_session(db, MON + timedelta(days=3), "plank", [(None, None, None)])  # sin reps ni duración: no cuenta
    got = weekly_hard_sets_by_muscle(db, MON)
    assert got == {"upper_back": 3.0, "chest": 2.0, "biceps": 1.5, "lats": 1.5, "delts": 1.0, "triceps": 1.0}


def test_e1rm_series_and_stored_value(db, cat):
    log_session(db, MON, "bb_bench", [(10, 60, None), (5, 70, 2)])
    ws = log_session(db, MON + timedelta(days=3), "bb_bench", [(8, 65, 2)])
    ws.sets[0].e1rm_kg = 90.0  # guardado al registrar: manda
    log_session(db, MON + timedelta(days=5), "bb_bench", [(20, 40, None)])  # r > 12: sin e1RM
    assert e1rm_series(db, "bb_bench") == [(MON, 86.3), (MON + timedelta(days=3), 90.0)]


def test_recent_prs_and_session_summary(db, cat):
    start = datetime(2026, 11, 9, 11, 0, tzinfo=UTC)
    ws = log_session(db, MON, "bb_bench", [(8, 60, 2), (6, 65, 1)], pr_flags={1: ["weight", "e1rm"]},
                     started_at=start)
    add_sets(db, ws, "db_curl", [(12, 10, 2)])
    ws.ended_at = start + timedelta(minutes=55)
    ws.session_rpe = 8
    log_session(db, MON + timedelta(days=2), "db_curl", [(13, 10, 2)], pr_flags={0: ["reps_at_weight"]})
    prs = recent_prs(db, limit=5)
    assert [(p["exercise_id"], p["flags"]) for p in prs] == [("db_curl", ["reps_at_weight"]),
                                                            ("bb_bench", ["weight", "e1rm"])]
    assert recent_prs(db, limit=1)[0]["exercise_id"] == "db_curl"
    summ = session_summary(db, ws.id)
    assert summ["duration_min"] == 55 and summ["sets"] == 3 and summ["exercises"] == 2
    assert summ["tonnage_kg"] == 480 + 390 + 240 and summ["session_rpe"] == 8
    assert [p["flags"] for p in summ["prs"]] == [["weight", "e1rm"]]


def test_session_summary_without_end_uses_last_set(db, cat):
    ws = log_session(db, MON, "db_bench", [(10, 20, 2)] * 3)  # sets a los 2, 4 y 6 min
    assert session_summary(db, ws.id)["duration_min"] == 6
    with pytest.raises(LookupError):
        session_summary(db, 9999)


def test_consistency_weeks_and_streak(db, cat):
    make_profile(db, days_per_week=2)
    today = date(2026, 11, 18)  # miércoles
    this_week = today - timedelta(days=today.weekday())
    for wk in (1, 2, 3):  # tres semanas cumplidas antes de la actual
        for d in (0, 2):
            log_session(db, this_week - timedelta(weeks=wk) + timedelta(days=d), "db_bench", [(10, 20, 2)])
    log_session(db, this_week - timedelta(weeks=5), "db_bench", [(10, 20, 2)])
    log_session(db, this_week, "db_bench", [(10, 20, 2)])
    c = consistency(db, weeks=12, today=today)
    assert c["target"] == 2 and len(c["weeks"]) == 12
    assert [w["count"] for w in c["weeks"][-6:]] == [1, 0, 2, 2, 2, 1]
    assert c["streak"] == 3  # la semana actual aún no se cumple, no rompe la racha
    log_session(db, today, "db_bench", [(10, 20, 2)])
    assert consistency(db, weeks=12, today=today)["streak"] == 4
    assert consistency(db, weeks=12, target_per_week=3, today=today)["streak"] == 0
    assert db.query(WorkoutSet).count() > 0
