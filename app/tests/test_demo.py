"""Synthetic demo seed: fills an empty database through the real services and every tab renders with it."""

from __future__ import annotations

from datetime import timedelta

import pytest
from factories import make_catalog
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from fitos.core.db import session_scope
from fitos.core.models import (
    BodyMetric,
    JobRun,
    LabReport,
    LabResult,
    PlannedSession,
    Profile,
    Program,
    WaMessage,
    WorkoutSession,
    WorkoutSet,
)
from fitos.core.timeutil import local_today, to_local
from fitos.demo import DemoError, seed_demo
from fitos.labs import service as labs
from fitos.main import app


def _count(db, model, *where) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where)) or 0


@pytest.fixture
def seeded():
    with session_scope() as db:
        make_catalog(db)
        summary = seed_demo(db, weeks=10, seed=7)
    return summary


def test_seed_produces_a_plausible_history(seeded):
    s = seeded
    assert 30 <= s.sessions <= 40 and s.missed >= 1
    assert s.pr_sets >= 2 and s.weight_increases >= 2
    assert s.lab_reports == 3 and s.lab_results >= 60
    with session_scope() as db:
        today = local_today()
        prof = db.get(Profile, 1)
        assert prof.onboarded and prof.height_cm == 178 and prof.days_per_week == 4
        sessions = db.scalars(select(WorkoutSession)).all()
        assert len(sessions) == s.sessions and all(ws.state == "completed" for ws in sessions)
        assert all(ws.date < today and to_local(ws.started_at).date() == ws.date for ws in sessions)
        assert _count(db, PlannedSession, PlannedSession.status == "skipped") == s.missed
        prs = db.scalars(select(WorkoutSet).where(func.json_array_length(WorkoutSet.pr_flags) > 0)).all()
        assert any(st.session.date >= today - timedelta(days=21) for st in prs)
        assert _count(db, WorkoutSet, WorkoutSet.rir.is_(None), WorkoutSet.reps.is_not(None)) == 0
        assert db.scalar(select(Program.rotation_idx)) is not None
        weights = db.scalars(select(BodyMetric.value).where(BodyMetric.kind == "weight_kg")
                             .order_by(BodyMetric.measured_on)).all()
        assert len(weights) > 40 and 0.8 < (sum(weights[:7]) - sum(weights[-7:])) / 7 < 2.5
        assert _count(db, WaMessage) == 0
        assert db.get(JobRun, ("morning_plan", today.isoformat())) is not None
        ov = labs.overview(db, prof.sex, prof.birth_date)
        assert ov["counts"]["markers"] >= 20
        assert ov["counts"]["out_of_target"] + ov["counts"]["borderline"] + ov["counts"]["out_of_range"] >= 2
        flagged = {i["key"] for i in labs.nutrition(db, prof.sex, prof.birth_date)}
        assert {"ldl_c", "vitamin_d_25oh", "apob"} <= flagged


def _fingerprint(db) -> list[tuple]:
    rows = db.execute(select(WorkoutSession.date, WorkoutSet.exercise_id, WorkoutSet.reps, WorkoutSet.weight_kg,
                             WorkoutSet.rir).join(WorkoutSession).order_by(WorkoutSession.date, WorkoutSet.id))
    return [tuple(r) for r in rows]


def test_seed_is_deterministic():
    with session_scope() as db:
        make_catalog(db)
        first = seed_demo(db, seed=3).as_dict()
        first_sets = _fingerprint(db)
    with session_scope() as db:
        second = seed_demo(db, seed=3, force=True).as_dict()
        assert _fingerprint(db) == first_sets
    assert first == second


def test_refuses_existing_data_unless_forced(seeded):
    with session_scope() as db, pytest.raises(DemoError):
        seed_demo(db)
    with session_scope() as db:
        again = seed_demo(db, weeks=4, force=True)
        assert again.sessions < seeded.sessions
        assert _count(db, LabReport) == 3 and _count(db, WorkoutSession) == again.sessions


def test_empty_catalog_fails_clearly():
    with session_scope() as db, pytest.raises(DemoError, match="catálogo"):
        seed_demo(db)


@pytest.mark.parametrize("path,needle", [
    ("/", "🏆"),
    ("/entrenos", "Torso"),
    ("/progreso", "e1RM"),
    ("/examenes", "Colesterol LDL"),
    ("/examenes/nutricion", "Avena"),
    ("/perfil", "Demo"),
    ("/examenes/ldl_c", "Limítrofe"),
])
def test_pages_render_with_demo(seeded, path, needle):
    with TestClient(app) as c:
        r = c.get(path)
        assert r.status_code == 200, r.text[:500]
        assert needle in r.text


def test_labs_overview_counts_markers(seeded):
    with TestClient(app) as c:
        html = c.get("/examenes").text
    with session_scope() as db:
        n = _count(db, LabResult, LabResult.report_id == db.scalar(select(func.max(LabReport.id))))
    assert n >= 20
    assert "marcadores" in html
