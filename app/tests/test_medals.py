"""Medals: gold, silver and bronze per exercise, one per workout day, by estimated 1RM, then weight and reps,
or reps for bodyweight; shown on the exercise, in the workout and in Progress."""

from __future__ import annotations

from datetime import date, timedelta

from factories import log_session, make_catalog
from fastapi.testclient import TestClient

from fitos.core.timeutil import local_today
from fitos.main import app
from fitos.training import medals

D = date(2026, 11, 1)


def test_podium_by_estimated_1rm_one_medal_per_day(db):
    make_catalog(db)
    log_session(db, D, "bb_bench", [(10, 60, 0), (8, 60, 0)])  # e1RM 80 (best set of that day)
    log_session(db, D + timedelta(days=2), "bb_bench", [(5, 70, 0), (3, 70, 0)])  # 81.7
    log_session(db, D + timedelta(days=4), "bb_bench", [(8, 60, 0)])  # 76
    log_session(db, D + timedelta(days=6), "bb_bench", [(6, 50, 0)])  # 60: off the podium
    db.flush()
    pod = medals.podium(db, "bb_bench")
    assert [(m.rank, m.weight_kg, m.reps, m.on) for m in pod] == [
        (1, 70, 5, D + timedelta(days=2)), (2, 60, 10, D), (3, 60, 8, D + timedelta(days=4))]
    assert pod[0].icon == "🥇" and pod[0].kind == "e1rm" and round(pod[0].score, 1) == 81.7


def test_tie_goes_to_the_earlier_day_and_bodyweight_counts_reps(db):
    make_catalog(db)
    log_session(db, D, "bb_bench", [(10, 60, 0)])
    log_session(db, D + timedelta(days=3), "bb_bench", [(10, 60, 0)])
    pod = medals.podium(db, "bb_bench")
    assert pod[0].on == D and pod[1].on == D + timedelta(days=3)
    log_session(db, D, "pushup", [(15, None, 2)])
    log_session(db, D + timedelta(days=1), "pushup", [(20, None, 1)])
    assert [(m.rank, m.reps) for m in medals.podium(db, "pushup")] == [(1, 20), (2, 15)]


def test_high_reps_rank_by_weight_then_reps(db):
    make_catalog(db)
    log_session(db, D, "bb_bench", [(15, 40, 0)])  # too many reps for Epley
    log_session(db, D + timedelta(days=1), "bb_bench", [(20, 40, 0)])
    log_session(db, D + timedelta(days=2), "bb_bench", [(12, 45, 0)])
    pod = medals.podium(db, "bb_bench")
    assert pod[0].kind == "weight" and [(m.weight_kg, m.reps) for m in pod] == [(45, 12), (40, 20), (40, 15)]


def test_medals_show_on_the_exercise_the_workout_and_progress(db):
    make_catalog(db)
    today = local_today()
    old = log_session(db, today - timedelta(days=60), "bb_bench", [(8, 60, 1)])
    new = log_session(db, today - timedelta(days=2), "bb_bench", [(8, 70, 1)])
    db.commit()
    held = medals.set_medals(db, ["bb_bench"])
    assert {m.rank for m in held.values()} == {1, 2}
    assert [m.rank for m, _ex in medals.recent(db, today - timedelta(days=30))] == [1]  # the old one is silver
    with TestClient(app) as c:
        detail = c.get("/ejercicios/bb_bench").text
        assert "Tu podio" in detail and "🥇" in detail and "🥈" in detail
        assert "🥇" in c.get(f"/entrenos/{new.id}").text and "🥈" in c.get(f"/entrenos/{old.id}").text
        prog = c.get("/progreso").text
        assert "Medallas del último mes" in prog and "/ejercicios/bb_bench#podio" in prog
