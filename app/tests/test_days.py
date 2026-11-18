"""Sick / travel days, finishing early, and the combined body-part split."""

from __future__ import annotations

from datetime import timedelta

import pytest
from factories import make_catalog, make_equipment_profiles, make_profile
from fastapi.testclient import TestClient
from sqlalchemy import select

import fitos.wa.session_flow as sf
from fitos.body import days
from fitos.core.db import session_scope
from fitos.core.models import DayLog, PlannedSession
from fitos.core.timeutil import local_now, local_today
from fitos.main import app
from fitos.training import service as tr
from fitos.training.planner import build_session, create_program_from_template
from fitos.training.weekly import week_report, week_start
from fitos.wa import outbox, session_flow
from fitos.wa.router import handle_incoming

DAY = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@pytest.fixture
def world():
    with session_scope() as db:
        make_catalog(db)
        _casa, gym = make_equipment_profiles(db)
        make_profile(db, sex="male", days_per_week=7, onboarded=True, injuries=[], disliked_exercise_ids=[],
                     schedule={d: {"equipment_profile_id": gym.id, "time": "06:30"} for d in DAY})
        create_program_from_template(db, None, 4)


async def say(text: str, fake) -> list[str]:
    from fitos.core.models import WaMessage

    n0 = len(fake.sent)
    with session_scope() as db:
        m = WaMessage(direction="in", chat_id="g", body=text, status="received", kind="text")
        db.add(m)
        db.flush()
        mid = m.id
    await handle_incoming(mid)
    await outbox.drain_once()
    return [s.text for s in fake.sent[n0:]]


async def test_sick_pauses_todays_plan_and_counts_streak(world, fake_wa):
    with session_scope() as db:
        plan = tr.get_or_build_plan(db, local_today())
        pid = plan.id
        days.mark(db, local_today() - timedelta(days=1), "sick", source="web")
    out = await say("estoy enfermo", fake_wa)
    assert out[0].startswith("🤒 Anotado: hoy enfermo.") and "Van 2 días seguidos" in out[0]
    with session_scope() as db:
        assert db.get(PlannedSession, pid).status == "skipped"
        assert days.streak(db, "sick") == 2
    out = await say("I'm sick", fake_wa)  # same day: still one row
    with session_scope() as db:
        assert len(db.scalars(select(DayLog)).all()) == 2
    assert "ya estoy bien" in out[0]
    out = await say("ya estoy bien", fake_wa)
    assert "baja ~10 %" in out[0]
    out = await say("de viaje", fake_wa)
    assert out[0].startswith("✈️") and days.between  # replaces today's mark
    with session_scope() as db:
        assert days.between(db, local_today(), local_today())[local_today()].kind == "travel"


async def test_have_to_go_finishes_and_lists_whats_left(world, fake_wa):
    await session_flow.handle_text(0, "1")
    await session_flow.handle_text(0, "10x40")
    await outbox.drain_once()
    out = await say("debo terminar", fake_wa)
    assert "de *1* a *10*" in out[0]
    out = await say("7", fake_wa)
    assert "Sesión cerrada" in out[0] and "Quedó pendiente:" in out[0]


async def test_morning_plan_respects_marked_days(world, fake_wa, monkeypatch):
    now = local_now().replace(hour=7, minute=0)
    monkeypatch.setattr(sf, "local_now", lambda: now)
    with session_scope() as db:
        days.mark(db, now.date(), "sick")
    assert "Enfermo" in await sf.send_morning_plan()
    with session_scope() as db:
        days.clear(db, now.date())
        days.mark(db, now.date() - timedelta(days=1), "sick")
    assert await sf.send_morning_plan() == "enviado"
    await outbox.drain_once()
    assert fake_wa.sent[-1].text.startswith("🤒 Estuviste enfermo hace poco")


def test_week_tab_marks_and_summary_counts(world):
    today = local_today()
    with TestClient(app) as c:
        r = c.post("/semana/estado", data={"date": today.isoformat(), "kind": "sick"}, follow_redirects=False)
        assert r.status_code == 303
        assert "🤒 Enfermo" in c.get(r.headers["location"]).text
        assert c.post("/semana/estado", data={"date": (today + timedelta(days=1)).isoformat(),
                                              "kind": "sick"}).status_code == 400
        with session_scope() as db:
            assert week_report(db, week_start(today))["days"] == {"sick": 1}
        c.post("/semana/estado", data={"date": today.isoformat(), "kind": ""})
    with session_scope() as db:
        assert days.between(db, today, today) == {}


def test_combo_split_pairs_groups(world):
    with session_scope() as db:
        prog = create_program_from_template(db, "combo_3", 4)
        assert [d.name for d in prog.days] == ["Pecho + hombro + bíceps", "Espalda + tríceps", "Pierna + abdomen"]
        plan = build_session(db, local_today() + timedelta(days=1), program_day_id=prog.days[0].id)
        muscles = {i.exercise.primary_muscle for i in plan.items}
        assert {"chest", "delts", "biceps"} <= muscles and "lats" not in muscles
