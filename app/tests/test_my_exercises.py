"""Progress → My exercises table, and the "didn't understand → here's what you can write" replies."""

from __future__ import annotations

from datetime import timedelta

import pytest
from factories import log_session, make_catalog, make_equipment_profiles, make_profile
from fastapi.testclient import TestClient

from fitos.core.db import session_scope
from fitos.core.timeutil import local_today
from fitos.main import app
from fitos.training import metrics
from fitos.training import service as tr
from fitos.training.planner import create_program_from_template
from fitos.wa import outbox, session_flow

DAY = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@pytest.fixture
def world():
    with session_scope() as db:
        make_catalog(db)
        _casa, gym = make_equipment_profiles(db)
        make_profile(db, sex="male", days_per_week=7, onboarded=True, injuries=[], disliked_exercise_ids=[],
                     schedule={d: {"equipment_profile_id": gym.id, "time": "06:30"} for d in DAY})
        create_program_from_template(db, None, 4)
        t = local_today()
        log_session(db, t - timedelta(days=60), "bb_bench", [(10, 50, 2), (8, 50, 2)], equipment_profile_id=gym.id)
        log_session(db, t - timedelta(days=10), "bb_bench", [(8, 60, 2)], equipment_profile_id=gym.id)
        log_session(db, t - timedelta(days=3), "pushup", [(12, None, 2), (15, None, 2)], equipment_profile_id=gym.id)
        log_session(db, t - timedelta(days=2), "pushup", [(10, None, 2)], equipment_profile_id=gym.id)


def test_table_rows(world):
    with session_scope() as db:
        rows = {r["id"]: r for r in metrics.exercise_table(db)}
        bench, push = rows["bb_bench"], rows["pushup"]
        assert bench["kind"] == "e1rm" and bench["sessions"] == 2 and bench["sets"] == 3
        assert bench["first_top"].weight_kg == 50 and bench["last_top"].weight_kg == 60
        assert bench["change_pct"] > 0 and bench["best"] == pytest.approx(bench["series"][-1][1])
        assert push["kind"] == "reps" and push["best"] == 15 and push["change_pct"] == pytest.approx(-33.3)
        assert metrics._score([], "weight") is None
        recent = {r["id"] for r in metrics.exercise_table(db, local_today() - timedelta(days=28))}
        assert recent == {"bb_bench", "pushup"}
        assert metrics.exercise_table(db, local_today() - timedelta(days=28))[0]["sessions"] in (1, 2)


def test_high_rep_lifts_track_the_top_weight(world):
    with session_scope() as db:
        log_session(db, local_today() - timedelta(days=20), "db_lateral", [(15, 8, 2)])
        log_session(db, local_today() - timedelta(days=5), "db_lateral", [(15, 9, 2)])
        row = next(r for r in metrics.exercise_table(db) if r["id"] == "db_lateral")
        assert row["kind"] == "weight" and [v for _d, v in row["series"]] == [8, 9] and row["change_pct"] == 12.5


def test_page_filters_and_sort(world):
    with TestClient(app) as c:
        html = c.get("/progreso/ejercicios").text
        assert "Mis ejercicios" in html and "bb_bench" in html and "pushup" in html and "<polyline" in html
        assert "2 ejercicios · 1 subieron · 1 bajaron" in html
        html = c.get("/progreso/ejercicios?musculo=chest&q=bench&orden=cambio&dir=asc").text
        assert "bb_bench" in html and "pushup" not in html
        assert "bb_bench" not in c.get("/progreso/ejercicios?periodo=4s&q=zzz").text
        assert "Mis ejercicios" in c.get("/progreso").text  # sub-nav on the summary too


async def _say(text, fake):
    n0 = len(fake.sent)
    await session_flow.handle_text(0, text)
    await outbox.drain_once()
    return [s.text for s in fake.sent[n0:]]


async def test_unknown_lists_what_you_can_do_now(world, fake_wa):
    with session_scope() as db:
        tr.get_or_build_plan(db, local_today())
    out = await _say("blablabla", fake_wa)
    assert out[0].startswith("No entendí 🤔.") and "*1* — empezar" in out[0] and "*4* — hoy descanso" in out[0]
    await _say("1", fake_wa)
    out = await _say("qué hago", fake_wa)
    assert "Ahora: *" in out[0] and "*debo terminar*" in out[0] and "*listo*" in out[0]
    out = await _say("hice 3 cosas raras", fake_wa)
    assert "No entendí el set" in out[0] and "*debo terminar*" in out[0]
    await _say("fin", fake_wa)
    await _say("7", fake_wa)
    out = await _say("blablabla", fake_wa)
    assert "Hoy ya entrenaste" in out[0] and "*enfermo*" in out[0]


def test_exercises_tab_colors_and_filters_by_usage(world):
    with session_scope() as db:
        t = local_today()
        log_session(db, t - timedelta(days=1), "pushup", [(12, None, 2)])  # 3 recent sessions → often
        usage = metrics.exercise_usage(db, t)
    assert usage["pushup"] == ("often", 3) and usage["bb_bench"] == ("rare", 1)  # 60 days ago is out
    with TestClient(app) as c:
        page = c.get("/ejercicios").text
        assert 'href="/ejercicios/pushup" data-usage="often"' in page
        assert 'href="/ejercicios/bb_bench" data-usage="rare"' in page and 'data-usage="never"' in page
        often = c.get("/ejercicios", params={"uso": "often"}).text
        assert "/ejercicios/pushup" in often and "/ejercicios/bb_bench" not in often
        rare = c.get("/ejercicios", params={"uso": "rare"}).text
        assert "/ejercicios/bb_bench" in rare and "/ejercicios/pushup" not in rare
        never = c.get("/ejercicios", params={"uso": "never"}).text
        assert "/ejercicios/pushup" not in never and "/ejercicios/bb_bench" not in never
        assert 'data-usage="never"' in never and 'data-usage="often"' not in never
