"""Conversación dorada del grupo (design §7.7): plan → empezar → sets → ocupada → salta → fin → RPE → resumen."""

from __future__ import annotations

from datetime import timedelta

import pytest
from factories import make_catalog, make_equipment_profiles, make_profile
from sqlalchemy import select

from fitos.core.db import session_scope
from fitos.core.models import PlannedSession, WaMessage, WorkoutSession, WorkoutSet
from fitos.core.timeutil import local_today, utcnow
from fitos.training import service as tr
from fitos.training.planner import create_program_from_template
from fitos.wa import outbox, session_flow

DAY = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@pytest.fixture
def world():
    with session_scope() as db:
        make_catalog(db)
        casa, gym = make_equipment_profiles(db)
        make_profile(db, sex="male", days_per_week=4, onboarded=True,
                     schedule={d: {"equipment_profile_id": gym.id, "time": "06:30"} for d in DAY},
                     injuries=[], disliked_exercise_ids=[])
        create_program_from_template(db, None, 4)
        return {"gym": gym.id, "casa": casa.id}


async def say(text: str, fake) -> list[str]:
    n0 = len(fake.sent)
    await session_flow.handle_text(0, text)
    await outbox.drain_once()
    return [s.text for s in fake.sent[n0:]]


async def test_golden_conversation(world, fake_wa):
    out = await say("hoy", fake_wa)
    assert out and "Hoy toca" in out[0] and "*1* Empezar" in out[0]

    out = await say("1", fake_wa)
    assert "1/" in out[0] and "Objetivo:" in out[0]

    with session_scope() as db:
        ws = tr.active_session(db)
        item = tr.current_item(db, ws)
        first_ex, n_sets = item.exercise_id, item.sets

    out = await say("10x40 r2", fake_wa)
    assert "Set 1: 10 reps" in out[0] and "Siguiente: set 2" in out[0]

    out = await say("ocupada", fake_wa)
    assert "estación ocupada" in out[0]
    with session_scope() as db:
        ws = tr.active_session(db)
        item = tr.current_item(db, ws)
        assert item.exercise_id != first_ex and item.swapped_from_id == first_ex
        busy = set(ws.wa_context["busy_stations"])
        assert item.exercise.station not in busy

    out = await say("salta", fake_wa)
    assert "Saltado" in out[0] and "2/" in out[0]

    out = await say("8, 8, 8" if n_sets == 3 else "8x20, 8x20, 8x20, 8x20", fake_wa)
    assert out

    out = await say("fin", fake_wa)
    assert "de *1* a *10*" in out[0]
    out = await say("7", fake_wa)
    assert "Sesión cerrada" in out[0]

    with session_scope() as db:
        ws = db.scalar(select(WorkoutSession).order_by(WorkoutSession.id.desc()))
        assert ws.state == "completed" and ws.session_rpe == 7
        assert ws.planned.status == "completed"
        prog = tr.active_program(db)
        assert prog.rotation_idx == 1
        assert db.scalar(select(WorkoutSet.id).where(WorkoutSet.session_id == ws.id)) is not None


async def test_rest_day_and_switch(world, fake_wa):
    out = await say("hoy", fake_wa)
    assert "Gym" in out[0]
    out = await say("2", fake_wa)
    assert "Casa" in out[0]
    with session_scope() as db:
        plan = tr.plan_for(db, local_today())
        assert plan.equipment_profile_id == world["casa"]
        reps = db.scalars(select(PlannedSession).where(PlannedSession.status == "replaced")).all()
        assert len(reps) == 1
    out = await say("4", fake_wa)
    assert "descanso" in out[0]
    with session_scope() as db:
        assert tr.plan_for(db, local_today()).status == "skipped"


async def test_body_weight_and_unknown(world, fake_wa):
    out = await say("peso 80,4", fake_wa)
    assert "80,4 kg" in out[0]
    out = await say("cuéntame un chiste", fake_wa)  # LLM_MODE=off → mensaje de ayuda
    assert "ayuda" in out[0]


async def test_parse_fail_and_undo(world, fake_wa):
    await say("1", fake_wa)
    out = await say("10x40", fake_wa)
    assert "Set 1" in out[0]
    out = await say("deshacer", fake_wa)
    assert "Borrado" in out[0]
    out = await say("12 x", fake_wa)  # sin peso y sin objetivo → pregunta, no registra
    assert "cuánto peso" in out[0] or "No entendí" in out[0]
    with session_scope() as db:
        assert len(tr.active_session(db).sets) == 0


async def test_idle_timeout_closes(world, fake_wa):
    await say("1", fake_wa)
    await say("10x40", fake_wa)
    with session_scope() as db:
        ws = tr.active_session(db)
        ws.last_activity_at = utcnow() - timedelta(minutes=50)
    assert await session_flow.check_timeouts() == "nudged"
    with session_scope() as db:
        tr.active_session(db).last_activity_at = utcnow() - timedelta(minutes=100)
    assert await session_flow.check_timeouts() == "closed"
    with session_scope() as db:
        assert tr.active_session(db) is None
        assert db.scalar(select(WaMessage.id).where(WaMessage.handler == "timeout")) is not None
