"""Rest alerts between sets and warm-up suggestions/logging in the WhatsApp session."""

from __future__ import annotations

from datetime import timedelta

import pytest
from factories import make_catalog, make_equipment_profiles, make_profile
from sqlalchemy import select

import fitos.jobs  # noqa: F401 — registers the timer handlers
from fitos.core import prefs
from fitos.core.db import session_scope
from fitos.core.models import Timer, WorkoutSet
from fitos.core.timeutil import local_today, utcnow
from fitos.scheduler import tick
from fitos.training import service as tr
from fitos.training.planner import create_program_from_template
from fitos.wa import outbox, session_flow

DAY = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@pytest.fixture
def world():
    with session_scope() as db:
        make_catalog(db)
        _casa, gym = make_equipment_profiles(db)
        make_profile(db, sex="male", days_per_week=4, onboarded=True, injuries=[], disliked_exercise_ids=[],
                     schedule={d: {"equipment_profile_id": gym.id, "time": "06:30"} for d in DAY})
        create_program_from_template(db, None, 4)
        tr.put_custom_plan(db, local_today(), "Pecho", [
            {"exercise": "bb_bench", "sets": 3, "reps": [6, 8], "weight_kg": 80},
            {"exercise": "db_incline", "sets": 2, "reps": [8, 10], "weight_kg": 22.5},
            {"exercise": "cable_fly", "sets": 2, "reps": [12, 15]},
        ])


async def say(text: str, fake) -> list[str]:
    n0 = len(fake.sent)
    await session_flow.handle_text(0, text)
    await outbox.drain_once()
    return [s.text for s in fake.sent[n0:]]


def _due_now():
    with session_scope() as db:
        for t in db.scalars(select(Timer).where(Timer.status == "pending")):
            t.due_at = utcnow() - timedelta(seconds=1)


async def test_warmup_only_on_the_first_heavy_lift_of_a_muscle(world, fake_wa):
    out = await say("1", fake_wa)
    assert "🔥 Calentamiento: 20 kg × 10 · 35 kg × 10 · 50 kg × 5 · 67,5 kg × 2" in out[0]
    out = await say("8x80, 8x80, 7x80", fake_wa)
    assert "2/3" in out[-1] and "Calentamiento" not in out[-1]  # chest is warm already


async def test_warmup_sets_are_logged_apart(world, fake_wa):
    await say("1", fake_wa)
    out = await say("c 10x20", fake_wa)
    assert "Calentamiento anotado" in out[0]
    out = await say("warmup 5x50", fake_wa)
    assert "Calentamiento anotado" in out[0]
    out = await say("8x80", fake_wa)
    assert "Set 1: 8 reps" in out[0] and "🏆" not in out[0]
    with session_scope() as db:
        sets = db.scalars(select(WorkoutSet).order_by(WorkoutSet.id)).all()
        assert [(s.is_warmup, s.set_idx) for s in sets] == [(True, 0), (True, 0), (False, 1)]


async def test_rest_alert_after_a_set_and_cancelled_by_the_next_one(world, fake_wa):
    with session_scope() as db:  # tick() also runs the daily jobs: keep the chat to the rest alerts
        prefs.set_(db, "weekly_summary_enabled", False)
        prefs.set_(db, "morning_enabled", False)
    await say("1", fake_wa)
    await say("8x80", fake_wa)
    with session_scope() as db:
        t = db.scalar(select(Timer).where(Timer.status == "pending"))
        assert t.kind == "rest" and 140 <= (t.due_at - utcnow()).total_seconds() <= 150  # compound: 150 s
    _due_now()
    await tick()
    await outbox.drain_once()
    assert any("⏱️ Descanso listo. Set 2/3 de *bb_bench*: 6–8 × 80 kg" in m.text for m in fake_wa.sent)

    await say("8x80", fake_wa)
    await say("7x80", fake_wa)  # finishes the exercise: the next rest is for the next exercise
    with session_scope() as db:
        pending = db.scalars(select(Timer).where(Timer.status == "pending")).all()
        assert len(pending) == 1
    await say("10x22,5", fake_wa)  # logging before the alert makes it stale
    n = len(fake_wa.sent)
    with session_scope() as db:
        for t in db.scalars(select(Timer).where(Timer.status == "pending")):
            t.payload = {**t.payload, "n_sets": -1}
    _due_now()
    await tick()
    await outbox.drain_once()
    assert not any("Descanso listo" in m.text for m in fake_wa.sent[n:])


async def test_rest_alert_can_be_turned_off(world, fake_wa):
    with session_scope() as db:
        prefs.set_(db, "rest_alert", False)
        prefs.set_(db, "warmup_sets", False)
    out = await say("1", fake_wa)
    assert "Calentamiento" not in out[0]
    await say("8x80", fake_wa)
    with session_scope() as db:
        assert db.scalar(select(Timer).where(Timer.status == "pending")) is None
