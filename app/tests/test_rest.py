"""Wearable features (readiness, illness warning, Progress → Rest) show only while a wearable is active; the
Rest charts and the illness detector work on synthetic days. WHOOP itself is never called."""

from __future__ import annotations

import json
import random
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from fitos.body import readiness, rest
from fitos.body import service as body
from fitos.core import prefs
from fitos.core.models import BodyMetric, WorkoutSession
from fitos.core.settings import get_settings
from fitos.core.timeutil import local_today
from fitos.integrations import common, wearables, whoop
from fitos.main import app


@pytest.fixture
def vault(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "secrets_key", "k" * 40)


def _onboarded(db) -> None:
    body.ensure_defaults(db).onboarded = True
    db.commit()


def _activate(db, latest: date, error: str | None = None) -> None:
    common.vault_write(whoop.VAULT, {"access_token": "a", "refresh_token": "r", "expires_at": 0})
    prefs.set_(db, whoop.STATUS_PREF, {"latest": latest.isoformat(), "last_error": error})


def _days(db, today: date, n: int = 60, seed: int = 7) -> None:
    rnd = random.Random(seed)
    for i in range(n):
        d = today - timedelta(days=n - 1 - i)
        vals = {"recovery_pct": rnd.randint(20, 95), "hrv_ms": rnd.gauss(85, 6), "resting_hr": rnd.gauss(56, 1.2),
                "skin_temp_c": rnd.gauss(33.7, 0.15), "respiratory_rate": rnd.gauss(15.9, 0.25),
                "sleep_hours": rnd.uniform(6, 8.5), "sleep_performance_pct": rnd.randint(60, 99),
                "deep_sleep_h": 1.5, "rem_sleep_h": 1.8, "light_sleep_h": 3.6, "awake_h": 0.5, "sleep_need_h": 8.2,
                "sleep_debt_h": 0.6, "bedtime_h": 23.4, "wake_h": 6.5, "strain": rnd.uniform(4, 17),
                "day_kcal": rnd.randint(1900, 2900), "hr_zone2_min": 20, "hr_zone4_min": 5}
        for k, v in vals.items():
            db.add(BodyMetric(kind=k, value=round(v, 2), measured_on=d, source="whoop"))
    db.flush()


def test_a_wearable_is_active_only_linked_healthy_and_recent(db, vault):
    today = local_today()
    assert wearables.active_source(db) is None  # nothing linked
    _activate(db, today)
    assert wearables.active_source(db) == "whoop"
    _activate(db, today, error="WHOOP respondió 401.")  # the membership ended: syncs fail
    assert wearables.active_source(db) is None
    _activate(db, today - timedelta(days=wearables.FRESH_DAYS + 1))  # not worn for a week
    assert wearables.active_source(db) is None


def test_no_active_wearable_hides_everything(db, vault):
    today = local_today()
    _days(db, today)  # data stays from before, but nothing is linked now
    assert readiness.morning_line(db, today) is None and readiness.ring(db, today) is None
    assert rest.build(db, today)["active"] is False
    _onboarded(db)
    with TestClient(app, follow_redirects=False) as c:
        assert "/progreso/descanso" not in c.get("/progreso").text
        assert c.get("/progreso/descanso").status_code == 303
        home = c.get("/")
        assert home.status_code == 200 and "/progreso/descanso" not in home.text


def test_illness_needs_two_signals_against_your_own_normal(db, vault):
    today = local_today()
    _days(db, today - timedelta(days=1), n=40)
    db.add_all([BodyMetric(kind="skin_temp_c", value=34.9, measured_on=today, source="whoop"),
                BodyMetric(kind="resting_hr", value=66, measured_on=today, source="whoop"),
                BodyMetric(kind="recovery_pct", value=12, measured_on=today, source="whoop")])
    db.flush()
    _activate(db, today)
    assert readiness.illness(db, today) == ["temperatura de la piel alta", "pulso en reposo alto"]
    line = readiness.morning_line(db, today)
    assert line.startswith("🤒") and "\n🔴" in line
    rows = readiness.rows_between(db, "whoop", today - timedelta(days=40), today)
    rows[today] = {**rows[today], "resting_hr": 56.0}  # one signal alone is not enough
    assert today not in readiness.illness_flags(rows, [today])


def test_rest_page_and_today_line_with_an_active_wearable(db, vault):
    today = local_today()
    _days(db, today, n=120)
    db.add(WorkoutSession(date=today - timedelta(days=2), state="completed"))
    _activate(db, today)
    _onboarded(db)
    data = rest.build(db, today, "30d")
    ch = data["charts"]
    assert data["days"] == 30 and len(ch["rec"]) == 30 and len(ch["rec7"]) == 30
    assert ch["trained"] == [(today - timedelta(days=2)).isoformat()]
    assert ch["bed"][0][1:] == [23.4, 7.1]  # 23:24 → 06:30
    assert ch["zones"]["z"][1] and ch["weekday"][0]["rec"] is not None and len(ch["hrv_band"]) == 30
    assert data["tiles"]["hrv"]["now"] is not None and data["tiles"]["need"] == 8.2
    assert len(rest.build(db, today, "todo")["charts"]["rec"]) == 120
    with TestClient(app, follow_redirects=False) as c:
        assert "/progreso/descanso" in c.get("/progreso").text
        page = c.get("/progreso/descanso?rango=1a")
        assert page.status_code == 200
        for chart in ("ch-rec", "ch-cal", "ch-hrv", "ch-sleep", "ch-bed", "ch-zones", "ch-weekday", "ch-month-hrv"):
            assert f'id="{chart}"' in page.text
        raw = page.text.split("const D = ", 1)[1].split(";\n", 1)[0]
        assert json.loads(raw)["rec"]
        assert "/progreso/descanso" in c.get("/").text  # today's readiness line links here


def test_charts_and_cards_say_where_their_data_comes_from(db, vault):
    from fitos.integrations import sources

    today = local_today()
    _days(db, today, n=20)
    db.add_all([BodyMetric(kind="weight_kg", value=81.2, measured_on=today, source="renpho"),
                BodyMetric(kind="weight_kg", value=81.0, measured_on=today - timedelta(days=1), source="wa"),
                BodyMetric(kind="muscle_pct", value=44.1, measured_on=today, source="renpho")])
    _activate(db, today)
    _onboarded(db)
    assert sources.used(db, ["weight_kg"]) == ["renpho"]  # typed weights get no badge
    assert sources.used(db, ["recovery_pct", "weight_kg"]) == ["whoop", "renpho"]
    assert sources.meta("wa") is None and sources.meta("whoop")["href"] == "/integraciones#whoop"
    with TestClient(app) as c:
        progress = c.get("/progreso").text
        assert 'href="/integraciones#renpho"' in progress and 'href="/integraciones#whoop"' in progress
        assert 'href="/integraciones#whoop"' in c.get("/progreso/descanso").text
        assert 'href="/integraciones#whoop"' in c.get("/").text
