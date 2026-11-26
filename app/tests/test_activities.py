"""Wearable activities: WHOOP workouts become rows, match the Fit-OS session they recorded, compare with the
week before, and feed the Activities pages, the session detail and the Rest tab. Synthetic data only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from fitos.body import activities, rest
from fitos.body import service as body
from fitos.core import prefs
from fitos.core.models import Activity, BodyMetric, WorkoutSession
from fitos.core.settings import get_settings
from fitos.core.timeutil import local_today
from fitos.integrations import common, whoop
from fitos.main import app


@pytest.fixture
def vault(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "secrets_key", "k" * 40)


def _workout(wid: str, start: datetime, minutes: int, sport: str = "powerlifting", strain: float = 12.0,
             kj: float = 2000.0, avg: int = 130, mx: int = 165) -> dict:
    return {"id": wid, "start": start.isoformat().replace("+00:00", "Z"),
            "end": (start + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z"),
            "timezone_offset": "-05:00", "sport_name": sport, "score_state": "SCORED",
            "score": {"strain": strain, "kilojoule": kj, "average_heart_rate": avg, "max_heart_rate": mx,
                      "zone_durations": {"zone_zero_milli": 60_000, "zone_one_milli": 600_000,
                                         "zone_two_milli": 1_200_000, "zone_three_milli": 900_000,
                                         "zone_four_milli": 300_000, "zone_five_milli": 0}}}


def _activate(db, today) -> None:
    common.vault_write(whoop.VAULT, {"access_token": "a", "refresh_token": "r", "expires_at": 0})
    prefs.set_(db, whoop.STATUS_PREF, {"latest": today.isoformat(), "last_error": None})


def test_workouts_become_activities_from_the_archive_and_upsert(db, vault):
    t = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)  # 07:00 local
    whoop.archive({"workout": [_workout("w1", t, 68), _workout("w2", t - timedelta(hours=3), 30, "walking", 5.1),
                               {**_workout("w3", t, 10), "score_state": "PENDING_SCORE"}]})
    assert whoop.sync_activities(db) == 2
    a = db.scalar(select(Activity).where(Activity.ext_id == "w1"))
    assert a.sport == "powerlifting" and a.kcal == pytest.approx(478.0, abs=0.1) and a.avg_hr == 130
    assert a.zones_min == [1.0, 10.0, 20.0, 15.0, 5.0, 0.0] and str(a.local_date) == "2026-11-25"
    assert whoop.sync_activities(db) == 0  # nothing changed
    whoop.archive({"workout": [_workout("w1", t, 68, strain=13.5)]})
    assert whoop.sync_activities(db) == 1 and db.get(Activity, a.id).strain == 13.5


def test_a_session_finds_the_activity_that_recorded_it_and_compares_the_week(db):
    t = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    rows = whoop.activity_rows([_workout("gym", t, 68), _workout("walk", t - timedelta(minutes=25), 20, "walking"),
                                _workout("old", t - timedelta(days=3), 60, kj=1500, avg=120, mx=160)])
    for r in rows:
        db.add(Activity(source="whoop", **r))
    ws = WorkoutSession(date=t.date(), started_at=t + timedelta(minutes=5), ended_at=t + timedelta(minutes=70),
                        last_activity_at=t + timedelta(minutes=70), state="completed")
    db.add(ws)
    db.flush()
    a = activities.for_session(db, ws)
    assert a.ext_id == "gym"
    cmp = activities.compare(db, a)
    assert cmp["days"] == 7 and cmp["n"] == 1 and cmp["avg_hr"] == 120 and cmp["kcal"] == pytest.approx(358.5, abs=0.1)
    d = activities.detail(a)
    assert d["minutes"] == 68 and d["has_zones"] and d["zones"][2]["pct"] == 39
    tot = activities.totals(list(db.scalars(select(Activity))))
    assert tot[0]["sport"] == "powerlifting" and tot[0]["n"] == 2 and tot[1]["label"] == "Caminar"


def test_pages_show_activities_only_with_an_active_wearable(db, vault):
    today = local_today()
    body.ensure_defaults(db).onboarded = True
    start = datetime.combine(today, datetime.min.time(), tzinfo=UTC) + timedelta(hours=12)
    for i in range(10):
        d = today - timedelta(days=i)
        for k, v in {"recovery_pct": 60 + i, "sleep_hours": 6 + i % 3, "sleep_performance_pct": 80,
                     "deep_sleep_h": 1.5, "rem_sleep_h": 1.8, "light_sleep_h": 3.6, "awake_h": 0.5,
                     "in_bed_h": 7.9, "sleep_disturbances": 10 + i, "strain": 12, "day_kcal": 2400}.items():
            db.add(BodyMetric(kind=k, value=v, measured_on=d, source="whoop"))
        whoop.archive({"workout": [_workout(f"w{i}", start - timedelta(days=i), 60)]})
    ws = WorkoutSession(date=today, started_at=start, ended_at=start + timedelta(minutes=60),
                        last_activity_at=start + timedelta(minutes=60), state="completed")
    db.add(ws)
    db.commit()
    with TestClient(app, follow_redirects=False) as c:
        assert c.get("/progreso/actividades").status_code == 303  # nothing linked yet
        assert "/progreso/actividades" not in c.get("/progreso").text
    _activate(db, today)
    whoop.sync_activities(db)
    db.commit()
    assert db.scalar(select(func.count()).select_from(Activity)) == 10
    data = rest.build(db, today, "30d")
    assert data["last_night"]["stats"][0]["kind"] == "sleep_hours" and len(data["charts"]["last_stages"]) == 4
    assert {b["label"]: b["n"] for b in data["buckets"]}["6–7 h"] >= 3
    assert data["charts"]["kcal_act"][-1][1] == 478 and data["charts"]["kcal_rest"][-1][1] == 1922
    assert data["charts"]["act_strain"][-1][2] == "Powerlifting"
    with TestClient(app) as c:
        page = c.get("/progreso/actividades?rango=30d").text
        assert "Powerlifting" in page and "tu sesión" in page and 'id="ch-zones-sport"' in page
        assert "Powerlifting" in c.get("/progreso/actividades?deporte=powerlifting").text
        a = db.scalar(select(Activity).where(Activity.ext_id == "w0"))
        det = c.get(f"/progreso/actividades/{a.id}").text
        assert "Zonas de tu pulso máximo" in det and f"/entrenos/{ws.id}" in det
        assert "Tu entreno según WHOOP" in c.get(f"/entrenos/{ws.id}").text
        r = c.get("/progreso/descanso")
        assert 'id="ch-night"' in r.text and 'id="ch-buckets"' in r.text and "Anoche" in r.text
