"""Editing and deleting past workouts from the Workouts tab (service + routes)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from factories import log_session, make_catalog, make_equipment_profiles, make_profile
from fastapi.testclient import TestClient
from sqlalchemy import select

from fitos.core.db import session_scope
from fitos.core.models import AuditLog, PlannedSession, WorkoutSession, WorkoutSet
from fitos.core.timeutil import local_today
from fitos.main import app
from fitos.training import service as tr
from fitos.training.planner import create_program_from_template

DAY = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@pytest.fixture
def world():
    with session_scope() as db:
        cat = make_catalog(db)
        _casa, gym = make_equipment_profiles(db)
        make_profile(db, sex="male", days_per_week=4, onboarded=True, injuries=[], disliked_exercise_ids=[],
                     schedule={d: {"equipment_profile_id": gym.id, "time": "06:30"} for d in DAY})
        create_program_from_template(db, None, 4)
        ex = next(e for e in cat.values() if e.load_kind == "total")
        today = local_today()
        a = log_session(db, today - timedelta(days=4), ex.id, [(8, 60, 2), (8, 60, 2)])
        b = log_session(db, today - timedelta(days=2), ex.id, [(8, 70, 2)])
        tr.recompute_prs(db, [ex.id])
        return {"ex": ex.id, "a": a.id, "b": b.id, "today": today}


def _flags(db, sid):
    return [s.pr_flags for s in db.scalars(select(WorkoutSet).where(WorkoutSet.session_id == sid)
                                           .order_by(WorkoutSet.id))]


def test_recompute_prs_follows_history(world):
    with session_scope() as db:
        assert _flags(db, world["a"]) == [[], []]  # first time: nothing to celebrate
        assert "weight" in _flags(db, world["b"])[0]


def test_deleting_the_older_workout_removes_the_later_pr(world):
    with TestClient(app) as c:
        r = c.post(f"/entrenos/{world['a']}/borrar", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/entrenos?deleted=1"
        assert "Entreno borrado." in c.get("/entrenos?deleted=1").text
    with session_scope() as db:
        assert db.get(WorkoutSession, world["a"]) is None
        assert db.scalars(select(WorkoutSet).where(WorkoutSet.session_id == world["a"])).all() == []
        assert _flags(db, world["b"]) == [[]]
        log = db.scalar(select(AuditLog).where(AuditLog.tool == "web_delete_session"))
        assert log and len(log.before["sets"]) == 2


def test_edit_session_fields_and_reject_future_date(world):
    sid, today = world["b"], world["today"]
    with TestClient(app) as c:
        r = c.post(f"/entrenos/{sid}/editar", data={"date": (today - timedelta(days=1)).isoformat(), "rpe": "8",
                                                     "duration_min": "55", "notes": "  felt strong  "},
                   follow_redirects=False)
        assert r.status_code == 303
        assert "Entreno guardado." in c.get(f"/entrenos/{sid}?saved=1").text
        bad = c.post(f"/entrenos/{sid}/editar", data={"date": (today + timedelta(days=1)).isoformat()})
        assert bad.status_code == 400
    with session_scope() as db:
        ws = db.get(WorkoutSession, sid)
        assert ws.date == today - timedelta(days=1) and ws.session_rpe == 8 and ws.notes == "felt strong"
        assert round((ws.ended_at - ws.started_at).total_seconds() / 60) == 55


def test_moving_a_workout_earlier_moves_the_pr(world):
    with session_scope() as db:
        ws = db.get(WorkoutSession, world["b"])
        tr.update_session(db, ws, on=world["today"] - timedelta(days=6), rpe=None, duration_min=None, notes=None)
    with session_scope() as db:
        assert _flags(db, world["b"]) == [[]]  # now the first one
        assert all(f == [] for f in _flags(db, world["a"]))  # 60 kg after 70 kg: no PR


def test_add_edit_delete_set(world):
    sid, ex = world["a"], world["ex"]
    with TestClient(app) as c:
        assert c.post(f"/entrenos/{sid}/sets", data={"exercise_id": ex, "reps": "6", "weight_kg": "62.5",
                                                      "rir": "1"}, follow_redirects=False).status_code == 303
        assert c.post(f"/entrenos/{sid}/sets", data={"exercise_id": ex, "reps": "0"}).status_code == 400
        assert c.post(f"/entrenos/{sid}/sets", data={"exercise_id": "nope", "reps": "5"}).status_code == 404
    with session_scope() as db:
        sets = db.scalars(select(WorkoutSet).where(WorkoutSet.session_id == sid).order_by(WorkoutSet.id)).all()
        assert [s.set_idx for s in sets][-1] == 3 and sets[-1].weight_kg == 62.5 and sets[-1].e1rm_kg
        first_id = sets[0].id
    with TestClient(app) as c:
        c.post(f"/entrenos/sets/{first_id}", data={"reps": "8", "delete": "1"})
    with session_scope() as db:
        sets = db.scalars(select(WorkoutSet).where(WorkoutSet.session_id == sid).order_by(WorkoutSet.id)).all()
        assert [s.set_idx for s in sets] == [1, 2]
        tr.update_set(db, sets[0], 10, 60, 2)
        assert sets[0].reps == 10
        with pytest.raises(ValueError):
            tr.update_set(db, sets[0], 10, 60, 42)


def test_deleting_todays_workout_reopens_its_plan_and_rotation(world):
    with session_scope() as db:
        prog = tr.active_program(db)
        idx0 = prog.rotation_idx
        plan = tr.get_or_build_plan(db, world["today"])
        ws = tr.start_session(db, plan)
        item = plan.items[0]
        from fitos.wa.parser import SetEntry

        tr.log_sets(db, ws, item, [SetEntry(reps=10, weight_kg=40, rir=2)])
        tr.finish_session(db, ws, rpe=7)
        assert prog.rotation_idx == (idx0 + 1) % len(prog.days)
        sid, pid = ws.id, plan.id
    with TestClient(app) as c:
        assert c.post(f"/entrenos/{sid}/borrar", follow_redirects=False).status_code == 303
    with session_scope() as db:
        plan = db.get(PlannedSession, pid)
        assert plan.status == "proposed" and all(it.status == "pending" for it in plan.items)
        assert tr.active_program(db).rotation_idx == idx0


def test_detail_page_has_editor_in_both_languages(world):
    from fitos.core import prefs

    with TestClient(app) as c:
        html = c.get(f"/entrenos/{world['a']}").text
        assert "Editar entreno" in html and "Borrar entreno" in html and "añadir set" in html
        with session_scope() as db:
            prefs.set_(db, "language", "en")
        html = c.get(f"/entrenos/{world['a']}").text
        assert "Edit workout" in html and "Delete workout" in html and "add set" in html


def test_empty_optional_numbers_from_forms_are_accepted():
    """Browsers send an empty kg/RIR as "": that is "no value", not a 422 (bodyweight sets)."""
    from datetime import date

    from factories import log_session, make_exercise
    from fastapi.testclient import TestClient

    from fitos.core.db import session_scope
    from fitos.main import app

    with session_scope() as db:
        make_exercise(db, "pushup", tag="bodyweight", pattern="push_horizontal")
        ws = log_session(db, date(2026, 11, 10), "pushup", [(12, None, 2)])
        sid, set_id = ws.id, ws.sets[0].id
    with TestClient(app, follow_redirects=False) as c:
        assert c.post(f"/entrenos/sets/{set_id}", data={"reps": "15", "weight_kg": "", "rir": ""}).status_code == 303
        assert c.post(f"/entrenos/{sid}/sets", data={"exercise_id": "pushup", "reps": "10", "weight_kg": "",
                                                     "rir": ""}).status_code == 303
