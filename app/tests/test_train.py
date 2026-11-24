"""Assisted training (Train tab): build the day from muscle groups or the program, respect the place and what's
missing today, adjust before and during, and log a whole workout from the web."""

from __future__ import annotations

import random
from datetime import date

import pytest
from factories import make_catalog, make_equipment_profiles, make_profile
from fastapi.testclient import TestClient

from fitos.core.db import session_scope
from fitos.core.models import PlannedSession, WorkoutSession
from fitos.main import app
from fitos.training import assist
from fitos.training import service as tr
from fitos.training.planner import create_program_from_template

DAY = date(2026, 11, 18)


@pytest.fixture
def world(db):
    make_profile(db, onboarded=True, schedule={"wed": {"equipment_profile_id": None, "time": "18:00"}})
    casa, gym = make_equipment_profiles(db)
    cat = make_catalog(db)
    db.commit()
    return {"casa": casa.id, "gym": gym.id, "cat": cat}


def _muscles(plan: PlannedSession) -> list[str]:
    return [it.exercise.primary_muscle for it in plan.items]


def test_groups_alternate_and_start_with_compounds(db, world):
    setup = assist.Setup(on=DAY, equipment_profile_id=world["gym"], focus="groups", groups=["chest", "shoulders"],
                         count=4, mode="smart")
    plan = assist.build(db, setup)
    assert _muscles(plan) == ["chest", "delts", "chest", "delts"]
    assert [it.exercise.mechanic for it in plan.items][:2] == ["compound", "compound"]
    assert plan.items[2].exercise.mechanic == "isolation" and plan.focus_label == "Pecho + Hombro"
    assert len({it.exercise_id for it in plan.items}) == 4  # no repeats


def test_home_and_missing_equipment_are_respected(db, world):
    home = assist.build(db, assist.Setup(on=DAY, equipment_profile_id=world["casa"], focus="groups",
                                         groups=["chest", "back"], count=4))
    assert all(it.exercise.equipment_tag in ("dumbbell", "bodyweight") for it in home.items)
    gym = assist.build(db, assist.Setup(on=DAY, equipment_profile_id=world["gym"], focus="groups",
                                        groups=["chest", "legs"], count=6, missing=["barbell", "cable"]))
    assert not {it.exercise.equipment_tag for it in gym.items} & {"barbell", "cable"}
    assert assist.today_missing(db, DAY) == {"barbell", "cable"}
    # alternatives for an item also skip what's missing
    opts = assist.options(db, gym.items[0])
    assert opts and not {e.equipment_tag for e in opts} & {"barbell", "cable"}


def test_random_mode_varies_but_stays_on_target(db, world):
    seen = set()
    for seed in range(8):
        plan = assist.build(db, assist.Setup(on=DAY, equipment_profile_id=world["gym"], focus="groups",
                                             groups=["chest"], count=2, mode="random"), rng=random.Random(seed))
        assert _muscles(plan) == ["chest", "chest"]
        seen.add(plan.items[0].exercise_id)
    assert len(seen) > 1


def test_program_focus_swaps_what_is_missing(db, world):
    create_program_from_template(db, "upper_lower_4", 4)
    plan = assist.build(db, assist.Setup(on=DAY, equipment_profile_id=world["gym"], focus="program",
                                         missing=["barbell"]))
    assert plan.items and all(it.exercise.equipment_tag != "barbell" for it in plan.items)


def test_reroll_add_remove_and_live_rules(db, world):
    plan = assist.build(db, assist.Setup(on=DAY, equipment_profile_id=world["gym"], focus="groups",
                                         groups=["chest"], count=2))
    first = plan.items[0]
    old = first.exercise_id
    assist.reroll(db, first, rng=random.Random(1))
    assert first.exercise_id != old and first.exercise.primary_muscle == "chest"
    added = assist.add(db, plan, group="biceps", rng=random.Random(2))
    assert added.exercise.primary_muscle == "biceps"
    assist.remove(db, added)
    assert len(plan.items) == 2
    ws = tr.start_session(db, plan, source="web")
    from fitos.wa.parser import SetEntry

    tr.log_sets(db, ws, plan.items[0], [SetEntry(reps=8, weight_kg=40)])
    with pytest.raises(ValueError):
        assist.reroll(db, plan.items[0])  # it already has sets
    with pytest.raises(ValueError):
        assist.remove(db, plan.items[0])
    live_add = assist.add(db, plan, exercise_id="pushdown")  # adding works mid-workout
    assert live_add in plan.items and plan.status == "active"
    assist.remove(db, plan.items[1])
    assert plan.items[1].status == "skipped"


def test_whole_workout_from_the_web(world):
    with TestClient(app, follow_redirects=False) as c:
        assert "Armemos tu entreno" in c.get("/entreno").text
        r = c.post("/entreno/armar", data={"equipment_profile_id": str(world["gym"]), "focus": "groups",
                                           "g_chest": "1", "g_triceps": "1", "count": "3", "mode": "smart",
                                           "date": DAY.isoformat()})
        assert r.status_code == 303 and r.headers["location"].startswith("/entreno?plan=")
        review = c.get(r.headers["location"])
        assert review.status_code == 200 and "Empezar" in review.text
        with session_scope() as s:
            plan = s.query(PlannedSession).filter_by(date=DAY, status="proposed").one()
            pid, items = plan.id, [it.id for it in plan.items]
        assert "usar" in c.get(f"/entreno/items/{items[0]}/opciones").text
        assert c.post("/entreno/empezar", data={"plan_id": pid}).headers["location"] == "/entreno"
        live = c.get("/entreno")
        assert "en curso" in live.text and "Anotar serie 1" in live.text
        r = c.post("/entreno/series", data={"item_id": items[0], "reps": "8", "weight_kg": "60", "rir": "2"})
        assert "descanso=" in r.headers["location"]
        assert "Anotar serie 2" in c.get("/entreno").text
        c.post(f"/entreno/items/{items[2]}", data={"action": "focus"})
        c.post("/entreno/series", data={"item_id": items[2], "reps": "12", "weight_kg": "20", "rir": ""})
        r = c.post("/entreno/series", data={"item_id": items[2], "reps": "10", "weight_kg": "", "rir": ""})
        assert "descanso=" in r.headers["location"]  # empty kg / RIR from the form are fine (bodyweight)
        assert c.post("/entreno/series", data={"item_id": items[2], "reps": "500"}).status_code == 303  # refused
        r = c.post("/entreno/terminar", data={"rpe": "8"})
        assert r.headers["location"].startswith("/entrenos/")
    with session_scope() as s:
        ws = s.query(WorkoutSession).one()
        assert ws.state == "completed" and ws.session_rpe == 8 and ws.date == DAY and ws.source == "web"
        assert sorted((st.reps, st.weight_kg or 0) for st in ws.sets) == [(8, 60.0), (10, 0), (12, 20.0)]


def test_manual_mode_starts_empty_and_needs_an_exercise(world):
    with TestClient(app, follow_redirects=False) as c:
        r = c.post("/entreno/armar", data={"equipment_profile_id": str(world["casa"]), "focus": "groups",
                                           "g_abs": "1", "mode": "manual"})
        page = c.get(r.headers["location"])
        assert "Todavía no hay ejercicios" in page.text
        pid = int(r.headers["location"].split("=")[1])
        assert "error=" in c.post("/entreno/empezar", data={"plan_id": pid}).headers["location"]
        assert "crunch" in c.get(f"/entreno/planes/{pid}/buscar", params={"q": "crunch"}).text
        c.post(f"/entreno/planes/{pid}/agregar", data={"exercise_id": "crunch"})
        assert c.post("/entreno/empezar", data={"plan_id": pid}).headers["location"] == "/entreno"


def test_setup_remembers_the_last_choice(db, world):
    assist.build(db, assist.Setup(on=DAY, equipment_profile_id=world["casa"], focus="groups",
                                  groups=["legs"], count=5, mode="random"))
    s = assist.last_setup(db, DAY)
    assert (s.equipment_profile_id, s.focus, s.groups, s.count, s.mode) == (world["casa"], "groups", ["legs"], 5, "random")


def test_variety_and_preferred_equipment(db, world):
    plan = assist.build(db, assist.Setup(on=DAY, equipment_profile_id=world["gym"], focus="groups",
                                         groups=["chest", "legs"], count=6, mode="smart"))
    tags = [it.exercise.equipment_tag for it in plan.items]
    assert max(tags.count(t) for t in set(tags)) <= assist.MAX_SAME_TAG + 1 and len(set(tags)) >= 3
    plan = assist.build(db, assist.Setup(on=DAY, equipment_profile_id=world["gym"], focus="groups",
                                         groups=["chest", "shoulders"], count=4, prefer=["dumbbell"]))
    assert all(it.exercise.equipment_tag == "dumbbell" for it in plan.items)
    # swaps list the best of each kind of equipment first
    opts = assist.options(db, plan.items[0])
    assert len({e.equipment_tag for e in opts[:3]}) >= 2


def test_remove_from_today_and_log_several_cards(world):
    with TestClient(app, follow_redirects=False) as c:
        r = c.post("/entreno/armar", data={"equipment_profile_id": str(world["gym"]), "focus": "groups",
                                           "g_chest": "1", "g_back": "1", "count": "4"})
        pid = int(r.headers["location"].split("=")[1])
        with session_scope() as s:
            items = [it.id for it in s.get(PlannedSession, pid).items]
        r = c.post(f"/entreno/items/{items[3]}", data={"action": "remove", "next": "/"})
        assert r.headers["location"] == "/"
        c.post("/entreno/empezar", data={"plan_id": pid})
        live = c.get("/entreno").text
        assert live.count('aria-label="Anotar serie 1"') == 3  # every pending card can take a set
        r = c.post("/entreno/series", data={"item_id": items[2], "reps": "10", "weight_kg": "30", "rir": ""})
        assert "descanso=" in r.headers["location"]
    with session_scope() as s:
        from fitos.core.models import Timer

        ws = s.query(WorkoutSession).one()
        assert [st.planned_item_id for st in ws.sets] == [items[2]]  # logged on a card that wasn't current
        assert s.query(Timer).filter_by(kind="rest", status="pending").count() == 1  # WhatsApp rest alert


def _program_plan(db, world):
    create_program_from_template(db, "upper_lower_4", 4)
    db.commit()
    return assist.build(db, assist.Setup(on=DAY, equipment_profile_id=world["gym"], focus="program"))


def test_done_from_today_logs_the_plan_and_partial_finish_keeps_the_rotation(db, world, fake_wa):
    from fitos.core import prefs
    from fitos.core.models import Timer, WaMessage

    plan = _program_plan(db, world)
    prog = tr.active_program(db)
    rot = prog.rotation_idx
    first = plan.items[0]
    db.commit()
    with TestClient(app, follow_redirects=False) as c:
        r = c.post(f"/entreno/items/{first.id}", data={"action": "done", "next": "/"})
        assert r.headers["location"] == "/"
        c.post("/entreno/nota", data={"sleep": "2", "energy": "2", "note": "dormí mal"})
        prefs.set_(db, "wa_mode", "reports")
        db.commit()
        item2 = plan.items[1]
        c.post("/entreno/series", data={"item_id": item2.id, "reps": "8", "weight_kg": "40", "rir": ""})
        r = c.post("/entreno/terminar", data={"rpe": "", "partial": "1"})
        assert r.headers["location"].startswith("/entrenos/")
    db.expire_all()
    ws = db.query(WorkoutSession).one()
    assert ws.state == "completed" and ws.notes == "Sueño 2/5 · Energía 2/5. dormí mal"
    planned = [st for st in ws.sets if st.planned_item_id == first.id]
    assert len(planned) == first.sets and {st.reps for st in planned} == {first.rep_min}
    assert tr.active_program(db).rotation_idx == rot  # partial: the same day comes back
    assert db.query(Timer).filter_by(kind="rest", status="pending").count() == 0  # reports only: no rest alerts
    assert db.query(WaMessage).filter_by(handler="summary").count() == 1  # but the day's report


def test_cardio_logs_duration_and_machine_details(db, world):
    plan = tr.put_custom_plan(db, DAY, "Cardio", [{"exercise": "goblet_squat", "sets": 1, "minutes": [15, 15]}])
    tr.start_session(db, plan, source="web")
    db.commit()
    item = plan.items[0]
    with TestClient(app, follow_redirects=False) as c:
        live = c.get("/entreno").text
        assert 'name="speed_kmh"' in live and 'name="steps"' in live
        c.post("/entreno/series", data={"item_id": item.id, "minutes": "15", "level": "", "speed_kmh": "4,5",
                                        "incline_pct": "15", "steps": "1000", "distance_km": ""})
        assert "4.5 km/h" in c.get("/entreno").text
    db.expire_all()
    st = db.query(WorkoutSession).one().sets[0]
    assert st.duration_s == 900 and st.extra == {"speed_kmh": 4.5, "incline_pct": 15.0, "steps": 1000}


async def test_idle_empty_session_is_discarded_not_completed(db, world):
    from datetime import timedelta

    from fitos.core.timeutil import utcnow
    from fitos.wa.session_flow import check_timeouts

    plan = _program_plan(db, world)
    rot = tr.active_program(db).rotation_idx
    ws = tr.start_session(db, plan, source="web")
    ws.last_activity_at = utcnow() - timedelta(hours=3)
    db.commit()
    assert await check_timeouts() == "discarded"
    db.expire_all()
    assert db.query(WorkoutSession).count() == 0
    assert tr.plan_for(db, DAY).status == "proposed"
    assert tr.active_program(db).rotation_idx == rot  # an empty session doesn't move the program


def test_reopen_add_and_finish_again_moves_the_program_once(db, world):
    plan = _program_plan(db, world)
    prog = tr.active_program(db)
    day_ids = [d.id for d in prog.days]
    expected = (day_ids.index(plan.program_day_id) + 1) % len(day_ids)
    first = plan.items[0]
    db.commit()
    with TestClient(app, follow_redirects=False) as c:
        c.post("/entreno/empezar", data={"plan_id": plan.id})
        c.post("/entreno/series", data={"item_id": first.id, "reps": "8", "weight_kg": "50", "rir": "2"})
        c.post("/entreno/terminar", data={"rpe": ""})
        sid = db.query(WorkoutSession).one().id
        assert "Reabrir" in c.get(f"/entrenos/{sid}").text
        assert c.post(f"/entrenos/{sid}/reabrir").headers["location"] == "/entreno"
        live = c.get("/entreno").text
        assert "en curso" in live and "Anotar serie 1" in live
        c.post(f"/entreno/planes/{plan.id}/agregar", data={"exercise_id": "pushdown"})
        db.expire_all()
        added = next(it for it in tr.plan_for(db, DAY).items if it.exercise_id == "pushdown")
        c.post("/entreno/series", data={"item_id": added.id, "reps": "12", "weight_kg": "25", "rir": "1"})
        c.post("/entreno/terminar", data={"rpe": "7"})
    db.expire_all()
    ws = db.query(WorkoutSession).one()
    assert ws.state == "completed" and len(ws.sets) == 2 and ws.session_rpe == 7
    assert tr.active_program(db).rotation_idx == expected  # not moved twice


def test_today_is_a_timeline_of_coming_and_previous_days(db, world):
    from datetime import timedelta

    from factories import log_session

    from fitos.core.timeutil import local_today

    today = local_today()
    future = tr.put_custom_plan(db, today + timedelta(days=2), "Pecho + hombro", [{"exercise": "db_bench", "sets": 3}])
    past = log_session(db, today - timedelta(days=1), "db_bench", [(10, 20, 2)] * 3)
    db.commit()
    with TestClient(app) as c:
        page = c.get("/").text
    assert "Próximos días" in page and f'href="/entreno?plan={future.id}"' in page
    assert "Días anteriores" in page and f'href="/entrenos/{past.id}"' in page
    assert f'action="/entrenos/{past.id}/reabrir"' in page


def test_skipped_items_are_hidden_and_rir_has_names(world):
    from fitos.core.timeutil import local_today

    today = local_today()
    with session_scope() as s:
        plan = assist.build(s, assist.Setup(on=today, equipment_profile_id=world["gym"], focus="groups",
                                            groups=["chest", "triceps"], count=3, mode="smart"))
        tr.start_session(s, plan, source="web")
        gone = plan.items[2]
        name = gone.exercise.display_name
        assert all(it.exercise.display_name != name for it in plan.items[:2])
        assist.remove(s, gone)
        assert gone.status == "skipped"
    with TestClient(app, follow_redirects=False) as c:
        live, home = c.get("/entreno").text, c.get("/").text
    assert name not in live and name not in home  # a dropped exercise is out of sight
    assert "1 · Muy duro" in live and "0 · Fallo" in live and "RIR = reps que te quedaban" in live


def test_saving_the_note_again_does_not_stack_sleep_and_energy():
    ws = WorkoutSession(date=DAY)
    assist.save_note(ws, "Todavía con gripa", 3, 3)
    assert ws.notes == "Sueño 3/5 · Energía 3/5. Todavía con gripa"
    assist.save_note(ws, ws.notes, 3, 3)  # the form comes back with the saved text
    assist.save_note(ws, "Sleep 3/5 · Energy 3/5. Sueño 3/5 · Energía 3/5. Todavía con gripa", 4, 3)
    assert ws.notes == "Sueño 4/5 · Energía 3/5. Todavía con gripa"
    assist.save_note(ws, ws.notes, None, None)  # radios not picked again: the saved values stay
    assert ws.notes == "Sueño 4/5 · Energía 3/5. Todavía con gripa"
    assist.save_note(ws, "Sueño 2/5. Mejor", None, 5)
    assert ws.notes == "Sueño 2/5 · Energía 5/5. Mejor"


def test_finish_an_exercise_with_fewer_sets_and_today_check_invents_nothing(db, world):
    from fitos.wa.parser import SetEntry

    plan = tr.put_custom_plan(db, DAY, "Espalda", [{"exercise": "pushdown", "sets": 4, "reps": [8, 10]},
                                                   {"exercise": "goblet_squat", "sets": 3, "reps": [8, 12]}])
    ws = tr.start_session(db, plan, source="web")
    first, second = plan.items
    assist.focus_on(db, ws, first)
    tr.log_sets(db, ws, first, [SetEntry(10, 25.0, 2), SetEntry(9, 25.0, 1)])
    db.commit()
    with TestClient(app, follow_redirects=False) as c:
        assert "Terminar este ejercicio" in c.get("/entreno").text
        assert "error=" in c.post(f"/entreno/items/{second.id}", data={"action": "finish"}).headers["location"]
        c.post(f"/entreno/items/{first.id}", data={"action": "finish"})
        live = c.get("/entreno").text  # what's left first; the finished one at the end, collapsed
        assert live.index(f'id="item-{second.id}"') < live.index("Hechos") < live.index(f'id="done-{first.id}"')
        c.post("/entreno/series", data={"item_id": second.id, "reps": "10", "weight_kg": "20", "rir": "2"})
        c.post(f"/entreno/items/{second.id}", data={"action": "done", "next": "/"})  # ✓ from Today
    db.expire_all()
    assert (first.status, second.status) == ("done", "done")
    assert len(tr.item_sets(ws, first)) == 2  # 2 of 4: closed as it was
    assert [(s.reps, s.weight_kg) for s in tr.item_sets(ws, second)] == [(10, 20.0)]  # nothing invented


def test_live_order_in_progress_then_untouched_then_done(db, world):
    from fitos.wa.parser import SetEntry

    plan = tr.put_custom_plan(db, DAY, "Mix", [{"exercise": "goblet_squat", "sets": 3, "reps": [8, 12]},
                                               {"exercise": "pushdown", "sets": 3, "reps": [8, 12]},
                                               {"exercise": "crunch", "sets": 3, "reps": [10, 15]}])
    ws = tr.start_session(db, plan, source="web")
    untouched, started, finished = plan.items
    tr.log_sets(db, ws, started, [SetEntry(10, 20.0, 2)])
    tr.log_sets(db, ws, finished, [SetEntry(15, None, 2)])
    assist.finish_item(db, ws, finished)
    db.commit()
    with TestClient(app, follow_redirects=False) as c:
        live = c.get("/entreno").text
    assert (live.index(f'id="item-{started.id}"') < live.index(f'id="item-{untouched.id}"')
            < live.index(f'id="done-{finished.id}"'))


def test_note_form_shows_saved_sleep_and_energy_checked(db, world):
    plan = tr.put_custom_plan(db, DAY, "Mix", [{"exercise": "pushdown", "sets": 3, "reps": [8, 12]}])
    ws = tr.start_session(db, plan, source="web")
    assist.save_note(ws, "Algo congestionado", 4, 3)
    db.commit()
    assert assist.parse_note(ws.notes) == (4, 3, "Algo congestionado")
    with TestClient(app, follow_redirects=False) as c:
        live = c.get("/entreno").text
    assert 'name="sleep" value="4" class="sr-only" checked' in live
    assert 'name="energy" value="3" class="sr-only" checked' in live
    assert ">Algo congestionado</textarea>" in live
