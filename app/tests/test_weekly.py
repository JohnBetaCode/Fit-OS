"""Sunday summary: numbers of the week, comparison with the previous one, what goes up, and the job."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from factories import log_session, make_catalog, make_equipment_profiles, make_profile

import fitos.core.timeutil as timeutil
from fitos import jobs
from fitos.body import service as body
from fitos.core.db import session_scope
from fitos.training import service as tr
from fitos.training.weekly import report_text, week_report, week_start
from fitos.wa import outbox

MON = date(2026, 11, 9)


@pytest.fixture
def world():
    with session_scope() as db:
        make_catalog(db)
        _casa, gym = make_equipment_profiles(db)
        make_profile(db, sex="male", days_per_week=3, onboarded=True, injuries=[], disliked_exercise_ids=[])
        log_session(db, MON - timedelta(days=6), "bb_bench", [(8, 70, 2), (8, 70, 2)], equipment_profile_id=gym.id)
        log_session(db, MON, "bb_bench", [(8, 75, 2), (8, 75, 2), (8, 75, 2)], equipment_profile_id=gym.id)
        log_session(db, MON + timedelta(days=2), "bb_squat", [(6, 100, 2), (6, 100, 2)], equipment_profile_id=gym.id)
        tr.recompute_prs(db, ["bb_bench", "bb_squat"])
        body.log_metric(db, "weight_kg", 81.0, MON - timedelta(days=7))
        body.log_metric(db, "weight_kg", 80.2, MON + timedelta(days=5))


def test_week_report_numbers(world):
    with session_scope() as db:
        r = week_report(db, MON)
    assert len(r["sessions"]) == 2 and r["target"] == 3
    assert (r["sets"], r["prev_sets"]) == (5, 2)
    assert r["tonnage"] == 8 * 75 * 3 + 6 * 100 * 2 and r["prev_tonnage"] == 8 * 70 * 2
    assert "bb_bench" in r["prs"] and "bb_squat" not in r["prs"]  # the squat had no history
    assert r["weight"] == {"kg": 80.2, "on": MON + timedelta(days=5), "delta": -0.8}
    assert r["low"]  # a few hard sets in each muscle: below the 10-set band


def test_report_text_and_ups(world):
    with session_scope() as db:
        r = week_report(db, MON)
        text = report_text(r, "https://fit.example:8444")
    assert text.startswith("📊 *Tu semana*") and "Entrenos: 2/3" in text and "(+150 %)" in text
    assert "⚖️ Peso: 80,2 kg (-0,8 kg)" in text and text.endswith("https://fit.example:8444/progreso")
    if r["ups"]:
        assert "Sube la próxima semana" in text


async def test_job_posts_only_on_sunday(world, fake_wa, monkeypatch):
    monkeypatch.setattr(timeutil, "local_today", lambda: MON + timedelta(days=1))
    assert await jobs.weekly_summary() == "no es domingo"
    monkeypatch.setattr(timeutil, "local_today", lambda: MON + timedelta(days=6))
    assert await jobs.weekly_summary() == "enviado"
    await outbox.drain_once()
    assert "Tu semana" in fake_wa.sent[-1].text and week_start(MON + timedelta(days=6)) == MON
