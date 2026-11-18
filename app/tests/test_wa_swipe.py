"""Hand-made plans and logging by replying to an exercise's card (WhatsApp swipe → reply)."""

from __future__ import annotations

import pytest
from factories import make_catalog, make_equipment_profiles, make_exercise, make_profile
from sqlalchemy import select

from fitos.core import prefs
from fitos.core.db import session_scope
from fitos.core.models import PlannedSession, WaMessage, WorkoutSet
from fitos.core.timeutil import local_today
from fitos.training import service as tr
from fitos.training.planner import create_program_from_template
from fitos.wa import cards, outbox, session_flow

DAY = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
ITEMS = [
    {"exercise": "bb_bench", "sets": 4, "reps": [6, 8], "rir": 2},
    {"exercise": "db_incline", "sets": 3, "reps": [8, 10], "rir": 2, "weight_kg": 22.5},
    {"exercise": "cable_fly", "sets": 3, "reps": [12, 15], "rir": 1},
    {"exercise": "treadmill_walk", "sets": 1, "minutes": [20, 25], "note": "zona 2"},
]


@pytest.fixture
def world():
    with session_scope() as db:
        make_catalog(db)
        make_exercise(db, "treadmill_walk", muscle="quads", pattern="cardio", tag="cardio_machine",
                      station="cardio_machine", load_kind="duration")
        _casa, gym = make_equipment_profiles(db)
        make_profile(db, sex="male", days_per_week=4, onboarded=True, injuries=[], disliked_exercise_ids=[],
                     schedule={d: {"equipment_profile_id": gym.id, "time": "06:30"} for d in DAY})
        create_program_from_template(db, None, 4)
        idx = tr.active_program(db).rotation_idx
        plan = tr.put_custom_plan(db, local_today(), "Pecho + cardio", ITEMS)
        return {"plan": plan.id, "items": [i.id for i in plan.items], "rotation": idx}


async def say(text: str, fake, quote: str | None = None) -> list[str]:
    n0 = len(fake.sent)
    with session_scope() as db:
        msg = WaMessage(direction="in", chat_id="g", body=text, status="received", quoted_wa_id=quote)
        db.add(msg)
        db.flush()
        mid = msg.id
    await session_flow.handle_text(mid, text)
    await outbox.drain_once()
    return [s.text for s in fake.sent[n0:]]


def _card_ids() -> dict[int, str]:
    with session_scope() as db:
        rows = db.execute(select(WaMessage.planned_item_id, WaMessage.wa_id)
                          .where(WaMessage.direction == "out", WaMessage.handler == "card")).all()
        return {iid: wid for iid, wid in rows}


def test_custom_plan_items_and_text(world):
    with session_scope() as db:
        plan = db.get(PlannedSession, world["plan"])
        assert plan.source == "custom" and plan.program_day_id is None and len(plan.items) == 4
        bench, incline, _fly, walk = plan.items
        assert (bench.sets, bench.rep_min, bench.rep_max) == (4, 6, 8) and bench.target_note  # calibration note
        assert incline.target_weight_kg == 22.5
        assert cards.rep_range(walk) == "20–25 min" and cards.is_timed(walk) and walk.target_note == "zona 2"
        assert "1 × 20–25 min" in cards.plan_message(plan, "Gym", "Casa", 60)
        # the plan the morning job or "hoy" finds is this one, not a planner-built one
        assert tr.get_or_build_plan(db, local_today()).id == plan.id


def test_custom_plan_replaces_a_proposed_one_and_validates(world):
    with session_scope() as db:
        new = tr.put_custom_plan(db, local_today(), "Otra", ITEMS[:1])
        assert db.get(PlannedSession, world["plan"]).status == "replaced"
        assert tr.plan_for(db, local_today()).id == new.id
        with pytest.raises(LookupError):
            tr.put_custom_plan(db, local_today(), "x", [{"exercise": "nope"}])
        with pytest.raises(ValueError):
            tr.put_custom_plan(db, local_today(), "x", [{"exercise": "bb_bench", "sets": 40}])


async def test_all_cards_and_swipe_reply(world, fake_wa):
    with session_scope() as db:
        prefs.set_(db, "wa_cards", "all")
    out = await say("1", fake_wa)
    assert "4 ejercicios" in out[0] and len(out) == 5
    assert all("Desliza este mensaje" in t for t in out[1:]) and "*25 min*" in out[4]
    ids = _card_ids()
    assert set(ids) == set(world["items"]) and all(ids.values())

    bench, _incline, fly, walk = world["items"]
    out = await say("12x15 r1", fake_wa, quote=ids[fly])  # out of order: the third exercise first
    assert out[0].startswith("*cable_fly* · Set 1: 12 reps")
    with session_scope() as db:
        ws = tr.active_session(db)
        assert tr.current_item(db, ws).id == fly
        sets = db.scalars(select(WorkoutSet).where(WorkoutSet.session_id == ws.id)).all()
        assert [(s.planned_item_id, s.reps, s.weight_kg) for s in sets] == [(fly, 12, 15)]
        statuses = {i.id: i.status for i in ws.planned.items}
        assert statuses[bench] == "pending" and statuses[fly] == "active"
        conf = db.scalar(select(WaMessage).where(WaMessage.handler == "set"))
        assert conf.planned_item_id == fly  # a reply to the confirmation also logs to that exercise

    out = await say("8x60, 8x60, 7x60, 6x60", fake_wa, quote=ids[bench])
    assert "*bb_bench* listo" in out[0] and "Sigue *2/4" in out[0]  # next is the first pending one

    out = await say("25 min", fake_wa, quote=ids[walk])
    assert "25 min" in out[0]
    with session_scope() as db:
        ws = tr.active_session(db)
        st = db.scalar(select(WorkoutSet).where(WorkoutSet.planned_item_id == walk))
        assert st.duration_s == 1500 and st.reps is None
        assert next(i for i in ws.planned.items if i.id == walk).status == "done"


async def test_reply_to_a_card_before_starting_starts_the_session(world, fake_wa):
    with session_scope() as db:
        assert tr.active_session(db) is None
        card = outbox.enqueue_text(db, "card", handler="card", planned_item_id=world["items"][2])
        card.wa_id, card.status = "CARD123", "sent"
    out = await say("12x15", fake_wa, quote="CARD123")
    assert "Set 1: 12 reps" in out[0]
    with session_scope() as db:
        ws = tr.active_session(db)
        assert ws is not None and ws.planned_session_id == world["plan"]
        assert tr.current_item(db, ws).id == world["items"][2]


async def test_one_by_one_mode_is_unchanged_and_custom_plans_keep_the_rotation(world, fake_wa):
    out = await say("1", fake_wa)
    assert len(out) == 1 and "1/4" in out[0] and "Desliza" not in out[0]
    await say("fin", fake_wa)
    out = await say("7", fake_wa)
    assert "Sesión cerrada" in out[0]
    with session_scope() as db:
        assert tr.active_program(db).rotation_idx == world["rotation"]


async def test_quote_of_an_unrelated_message_is_a_normal_message(world, fake_wa):
    await say("1", fake_wa)
    out = await say("10x40", fake_wa, quote="SOMETHING-ELSE")
    assert "Set 1: 10 reps" in out[0] and not out[0].startswith("*")
