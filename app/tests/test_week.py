"""Week tab: projected rotation, building/resting days and editing a plan before it starts."""

from __future__ import annotations

from datetime import timedelta

import pytest
from factories import make_catalog, make_equipment_profiles, make_profile
from fastapi.testclient import TestClient
from sqlalchemy import select

from fitos.core.db import session_scope
from fitos.core.models import PlannedSession
from fitos.core.timeutil import local_today
from fitos.main import app
from fitos.training import service as tr
from fitos.training import week as wk
from fitos.training.planner import create_program_from_template

DAY = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@pytest.fixture
def world():
    with session_scope() as db:
        make_catalog(db)
        _casa, gym = make_equipment_profiles(db)
        make_profile(db, sex="male", days_per_week=7, onboarded=True, injuries=[], disliked_exercise_ids=[],
                     schedule={d: {"equipment_profile_id": gym.id, "time": "06:30"} for d in DAY})
        create_program_from_template(db, None, 4)
        return {"gym": gym.id}


def _names(db):
    return [d.name for d in tr.active_program(db).days]


def test_projection_follows_the_rotation_and_skips_rest_days(world):
    today = local_today()
    with session_scope() as db:
        names = _names(db)
        proj = wk.projected_days(db, today, 4)
        assert [proj[today + timedelta(days=i)].name for i in range(4)] == [names[i % len(names)] for i in range(4)]
        wk.build_day(db, today)
        wk.rest_day(db, today)  # resting today: tomorrow takes today's program day
        proj = wk.projected_days(db, today, 3)
        assert today not in proj and proj[today + timedelta(days=1)].name == names[0]


def test_build_a_future_day_with_a_chosen_focus_and_edit_it(world):
    tomorrow = local_today() + timedelta(days=1)
    with session_scope() as db:
        day = tr.active_program(db).days[2]
        plan = wk.build_day(db, tomorrow, day.id)
        assert plan.program_day_id == day.id and plan.date == tomorrow and plan.items
        first, n = plan.items[0], len(plan.items)
        wk.update_item(db, first, sets=5, rep_min=5, rep_max=5, rir=1, weight_kg=90)
        assert (first.sets, first.rep_min, first.target_weight_kg) == (5, 5, 90)
        wk.move_item(db, first, 1)
        assert plan.items[1].id == first.id and [i.order_idx for i in plan.items] == list(range(n))
        added = wk.add_item(db, plan, "plank")
        assert plan.items[-1].id == added.id
        wk.remove_item(db, added)
        assert len(plan.items) == n
        alt = next(e for e, _s in tr.alternatives(db, None, first, limit=3))
        wk.swap_item(db, first, alt.id)
        assert first.exercise_id == alt.id and first.swapped_from_id
        with pytest.raises(ValueError):
            wk.update_item(db, first, sets=50, rep_min=5, rep_max=5, rir=1, weight_kg=None)


def test_started_plans_are_read_only(world):
    with session_scope() as db:
        plan = tr.get_or_build_plan(db, local_today())
        tr.start_session(db, plan)
        with pytest.raises(ValueError):
            wk.update_item(db, plan.items[0], sets=3, rep_min=8, rep_max=10, rir=2, weight_kg=None)
        with pytest.raises(ValueError):
            wk.build_day(db, local_today())


def test_week_page_and_routes(world):
    tomorrow = local_today() + timedelta(days=1)
    with TestClient(app) as c:
        html = c.get("/semana").text
        assert "Sets planeados" not in html and "Toca:" in html and ">Semana<" in html
        r = c.post("/semana/dia", data={"date": tomorrow.isoformat(), "action": "build"}, follow_redirects=False)
        assert r.status_code == 303 and f"#d-{tomorrow.isoformat()}" in r.headers["location"]
        with session_scope() as db:
            plan = tr.plan_for(db, tomorrow)
            pid, iid = plan.id, plan.items[0].id
        html = c.get(r.headers["location"]).text
        assert "Sets planeados por músculo" in html and "editar" in html
        assert c.post(f"/semana/items/{iid}", data={"action": "save", "sets": "4", "rep_min": "6", "rep_max": "8",
                                                     "rir": "2", "weight_kg": "60"}).status_code == 200
        assert c.post(f"/semana/items/{iid}", data={"action": "save", "sets": "0"}).status_code == 400
        assert "plank" in c.get(f"/semana/planes/{pid}/buscar?q=plank").text
        assert "usar" in c.get(f"/semana/items/{iid}/opciones").text
        assert c.post(f"/semana/planes/{pid}/items", data={"exercise_id": "plank"}).status_code == 200
        assert c.post("/semana/dia", data={"date": tomorrow.isoformat(), "action": "rest"}).status_code == 200
        past = local_today() - timedelta(days=1)
        assert c.post("/semana/dia", data={"date": past.isoformat()}).status_code == 400
    with session_scope() as db:
        assert db.get(PlannedSession, pid).status == "skipped"
        assert db.scalars(select(PlannedSession).where(PlannedSession.date == tomorrow)).all()
