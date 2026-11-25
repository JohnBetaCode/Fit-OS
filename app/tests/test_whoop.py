"""WHOOP: OAuth with state, encrypted tokens that rotate, paging, records turned into daily values, the
Integrations page, and recovery in the morning message and Progress. WHOOP itself is never called."""

from __future__ import annotations

import json
import stat
import time
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from fitos.body import readiness
from fitos.core import prefs, tenancy
from fitos.core.db import session_scope
from fitos.core.models import BodyMetric
from fitos.core.settings import get_settings
from fitos.core.timeutil import local_today
from fitos.integrations import common, whoop
from fitos.main import app

SLEEPS = [
    {"id": "s-1", "start": "2026-11-24T04:30:00.000Z", "end": "2026-11-24T11:10:00.000Z", "timezone_offset": "-05:00",
     "nap": False, "score_state": "SCORED",
     "score": {"stage_summary": {"total_light_sleep_time_milli": 12_600_000, "total_slow_wave_sleep_time_milli": 5_400_000,
                                 "total_rem_sleep_time_milli": 3_600_000},
               "sleep_performance_percentage": 82, "sleep_efficiency_percentage": 91.7, "respiratory_rate": 15.2}},
    {"id": "s-nap", "start": "2026-11-24T19:00:00.000Z", "end": "2026-11-24T19:40:00.000Z",
     "timezone_offset": "-05:00", "nap": True, "score_state": "SCORED", "score": {}},
    {"id": "s-0", "start": "2026-11-23T05:00:00.000Z", "end": "2026-11-23T09:00:00.000Z", "timezone_offset": "-05:00",
     "nap": False, "score_state": "PENDING_SCORE"},
]
RECOVERIES = [
    {"cycle_id": 10, "sleep_id": "s-1", "score_state": "SCORED",
     "score": {"recovery_score": 72, "hrv_rmssd_milli": 61.4, "resting_heart_rate": 52, "spo2_percentage": 96.5,
               "skin_temp_celsius": 33.1, "user_calibrating": False}},
    {"cycle_id": 9, "sleep_id": "s-0", "score_state": "SCORED", "score": {"recovery_score": 30}},  # its sleep isn't scored
]
CYCLES = [{"id": 10, "start": "2026-11-24T04:30:00.000Z", "timezone_offset": "-05:00", "score_state": "SCORED",
           "score": {"strain": 12.4, "kilojoule": 10460.0, "average_heart_rate": 70, "max_heart_rate": 160}}]
DAY = date(2026, 11, 24)


@pytest.fixture
def setup(monkeypatch, tmp_path):
    s = get_settings()
    for k, v in {"data_dir": tmp_path, "secrets_key": "k" * 40, "whoop_client_id": "cid",
                 "whoop_client_secret": "csecret", "portal_base_url": "https://fit.example"}.items():
        monkeypatch.setattr(s, k, v)
    return tmp_path


def test_records_become_days_by_the_morning_you_woke_up():
    days = whoop.daily(RECOVERIES, SLEEPS, CYCLES)
    d = days[DAY]  # 11:10Z at -05:00 is 06:10 on the 24th
    assert d["recovery_pct"] == 72 and d["hrv_ms"] == 61.4 and d["resting_hr"] == 52
    assert d["sleep_hours"] == 6.0 and d["sleep_performance_pct"] == 82 and d["respiratory_rate"] == 15.2
    assert d["strain"] == 12.4 and d["day_kcal"] == 2500
    assert set(days) == {DAY}  # the unscored sleep and the nap add nothing


def test_a_cycle_belongs_to_the_day_you_woke_up_not_the_night_it_started():
    # The cycle starts when you fall asleep: 23:30 local on the 23rd is still the 24th's strain.
    cycles = [{**CYCLES[0], "start": "2026-11-24T04:30:00.000Z"}]
    assert whoop.daily(RECOVERIES, SLEEPS, cycles)[DAY]["strain"] == 12.4
    # Without a recovery to anchor it (today's cycle before scoring), start + 12 h decides.
    lone = [{"id": 99, "start": "2026-11-25T03:45:00.000Z", "timezone_offset": "-05:00", "score_state": "SCORED",
             "score": {"strain": 3.1, "kilojoule": 2000.0}}]  # 22:45 on the 24th
    assert whoop.daily([], [], lone)[date(2026, 11, 25)]["strain"] == 3.1


def test_oauth_state_tokens_and_rotation(db, setup):
    url = whoop.authorize_url(db)
    q = parse_qs(urlparse(url).query)
    assert url.startswith(whoop.AUTH_URL) and q["client_id"] == ["cid"] and "offline" in q["scope"][0]
    assert q["redirect_uri"] == ["https://fit.example/integraciones/whoop/callback"] and len(q["state"][0]) >= 8
    seen: list[dict] = []

    def token_server(req: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(req.content.decode()).items()}
        seen.append(form)
        n = len(seen)
        return httpx.Response(200, json={"access_token": f"acc{n}", "refresh_token": f"ref{n}", "expires_in": 3600,
                                         "scope": "offline read:recovery", "token_type": "bearer"})

    http = httpx.Client(transport=httpx.MockTransport(token_server))
    with pytest.raises(whoop.WhoopError):
        whoop.finish_link(db, "code", "not-the-state", http)  # and the pending state is gone after one try
    url = whoop.authorize_url(db)
    state = parse_qs(urlparse(url).query)["state"][0]
    with tenancy.as_user("tester"):
        whoop.finish_link(db, "the-code", state, http)
        assert seen[-1]["grant_type"] == "authorization_code" and seen[-1]["code"] == "the-code"
        assert seen[-1]["client_secret"] == "csecret"
        p = common.vault_path("whoop")
        assert stat.S_IMODE(p.stat().st_mode) == 0o600 and "acc1" not in p.read_text()
        assert whoop.access_token(http) == "acc1"  # still valid: no refresh
        tok = common.vault_read("whoop")
        common.vault_write("whoop", {**tok, "expires_at": int(time.time()) - 1})
        assert whoop.access_token(http) == "acc2"
        assert seen[-1]["grant_type"] == "refresh_token" and seen[-1]["refresh_token"] == "ref1"
        assert common.vault_read("whoop")["refresh_token"] == "ref2"  # rotated and saved


def test_expired_state_is_refused(db, setup):
    whoop.authorize_url(db)
    st = prefs.get(db, whoop.STATE_PREF)
    prefs.set_(db, whoop.STATE_PREF, {**st, "at": st["at"] - whoop.STATE_TTL_S - 1})
    with pytest.raises(whoop.WhoopError):
        whoop.finish_link(db, "c", st["state"])


def test_client_pages_and_refreshes_once_on_401(setup):
    calls: list[tuple[str, dict, str]] = []

    def api(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/oauth2/token"):
            return httpx.Response(200, json={"access_token": "fresh", "refresh_token": "r2", "expires_in": 3600})
        auth = req.headers["authorization"]
        params = dict(req.url.params)
        calls.append((req.url.path, params, auth))
        if auth == "Bearer stale":
            return httpx.Response(401)
        if "nextToken" not in params:
            return httpx.Response(200, json={"records": [{"n": 1}], "next_token": "p2"})
        return httpx.Response(200, json={"records": [{"n": 2}], "next_token": None})

    with tenancy.as_user("tester"):
        common.vault_write("whoop", {"access_token": "stale", "refresh_token": "r1",
                                     "expires_at": int(time.time()) + 3000})
        client = whoop.WhoopClient(httpx.Client(transport=httpx.MockTransport(api)))
        rows = client.collection("/v2/recovery", datetime(2026, 11, 1))
    assert rows == [{"n": 1}, {"n": 2}]
    assert calls[0][2] == "Bearer stale" and calls[1][2] == "Bearer fresh"
    assert calls[1][1]["limit"] == "25" and calls[-1][1]["nextToken"] == "p2"


def test_store_writes_only_changes_and_the_next_sync_overlaps(db):
    fake = whoop.FakeWhoop({"/v2/recovery": RECOVERIES, "/v2/activity/sleep": SLEEPS, "/v2/cycle": CYCLES})
    assert whoop.since_for(db, DAY) == DAY - timedelta(days=whoop.BACKFILL_DAYS)
    res = whoop.store(db, whoop.fetch(fake, DAY - timedelta(days=30)))
    assert res.written == 18 and res.latest == DAY  # 11 basics + sleep stages, bed/wake times, day HR
    assert whoop.store(db, whoop.fetch(fake, DAY)).written == 0
    assert whoop.since_for(db, DAY + timedelta(days=1)) == DAY - timedelta(days=whoop.OVERLAP_DAYS)
    assert len(db.scalars(select(BodyMetric).where(BodyMetric.source == "whoop")).all()) == 18


def test_readiness_bands_morning_line_and_ring(db, setup):
    today = local_today()
    common.vault_write(whoop.VAULT, {"access_token": "a", "refresh_token": "r", "expires_at": 0})
    prefs.set_(db, whoop.STATUS_PREF, {"latest": today.isoformat()})
    for k, v in {"recovery_pct": 28, "hrv_ms": 40, "sleep_hours": 5.2}.items():
        db.add(BodyMetric(kind=k, value=v, measured_on=today, source="whoop"))
    db.flush()
    line = readiness.morning_line(db, today)
    assert line.startswith("🔴") and "28 %" in line and "5.2 h" in line
    r = readiness.ring(db, today)
    assert r["pct"] == 28 and r["band"] == "red" and "VFC 40 ms" in r["note"]
    assert readiness.morning_line(db, today - timedelta(days=5)) is None
    assert readiness.band(70) == "green" and readiness.band(50) == "yellow"


def test_integrations_page_link_flow(setup, monkeypatch):
    from fitos.web import routes_integrations

    monkeypatch.setattr(whoop, "_token_request", lambda form, http=None: {
        "access_token": "a", "refresh_token": "r", "expires_at": int(time.time()) + 3000, "scope": "offline"})
    monkeypatch.setattr(routes_integrations, "whoop_client", lambda: whoop.FakeWhoop(
        {"/v2/recovery": RECOVERIES, "/v2/activity/sleep": SLEEPS, "/v2/cycle": CYCLES}))
    with TestClient(app, follow_redirects=False) as c:
        assert "Conectar con WHOOP" in c.get("/integraciones").text
        r = c.post("/integraciones/whoop/conectar")
        assert r.status_code == 303 and r.headers["location"].startswith(whoop.AUTH_URL)
        state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
        r = c.get("/integraciones/whoop/callback", params={"code": "x", "state": state})
        assert r.headers["location"] == "/integraciones#whoop"
        page = c.get("/integraciones").text
        assert "Sincronizar ahora" in page and "1 días con datos" in page
        assert c.get("/integraciones/whoop/callback", params={"error": "access_denied"}).status_code == 303
        c.post("/integraciones/whoop/desconectar")
        assert "Conectar con WHOOP" in c.get("/integraciones").text
    with session_scope() as db:
        assert db.scalar(select(BodyMetric).where(BodyMetric.source == "whoop", BodyMetric.kind == "recovery_pct"))


def test_not_configured_shows_the_admin_how(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "whoop_client_id", "")
    with TestClient(app) as c:
        page = c.get("/integraciones").text
    assert "falta configurar" in page and "/integraciones/whoop/callback" in page


async def test_scheduled_sync_does_nothing_when_not_linked(setup):
    from fitos.jobs import whoop_sync

    with tenancy.as_user("tester"):
        assert await whoop_sync() is None


def test_vault_survives_and_is_per_integration(setup):
    with tenancy.as_user("tester"):
        common.vault_write("whoop", {"a": 1})
        assert common.vault_read("whoop") == {"a": 1}
        raw = json.loads(common.vault_path("whoop").read_text())
        common.vault_path("other").write_text(json.dumps(raw))  # another integration's key can't open it
        with pytest.raises(common.IntegrationError):
            common.vault_read("other")


def test_admin_enters_the_app_credentials_on_the_web(monkeypatch, tmp_path):
    s = get_settings()
    for k, v in {"data_dir": tmp_path, "secrets_key": "k" * 40, "whoop_client_id": "", "whoop_client_secret": "",
                 "portal_base_url": "https://fit.example"}.items():
        monkeypatch.setattr(s, k, v)
    with TestClient(app, follow_redirects=False) as c:
        assert "Pega aquí su Client ID y Client Secret" in c.get("/integraciones").text
        r = c.post("/integraciones/whoop/app", data={"client_id": " my-client-id ", "client_secret": "s3cr3t-value"})
        assert r.headers["location"] == "/integraciones#whoop"
        page = c.get("/integraciones").text
        assert "Credenciales guardadas" in page and "desde la web" in page and "s3cr3t-value" not in page
        assert "Conectar con WHOOP" in page
    assert whoop.configured() and whoop.app_credentials() == ("my-client-id", "s3cr3t-value")
    p = tmp_path / "integrations" / "whoop_app.json"
    assert stat.S_IMODE(p.stat().st_mode) == 0o600 and "s3cr3t-value" not in p.read_text()
    monkeypatch.setattr(s, "whoop_client_id", "env-id")
    monkeypatch.setattr(s, "whoop_client_secret", "env-secret")
    assert whoop.app_credentials() == ("env-id", "env-secret") and whoop.app_source() == "env"  # .env wins
    monkeypatch.setattr(s, "whoop_client_id", "")
    with TestClient(app, follow_redirects=False) as c:
        c.post("/integraciones/whoop/app/quitar")
    assert not whoop.configured()


WORKOUTS = [
    {"id": "w-1", "start": "2026-11-24T22:00:00.000Z", "end": "2026-11-24T23:15:00.000Z", "timezone_offset": "-05:00",
     "sport_name": "weightlifting", "score_state": "SCORED",
     "score": {"strain": 9.8, "kilojoule": 1673.6, "distance_meter": None,
               "zone_durations": {"zone_one_milli": 1_800_000, "zone_two_milli": 1_200_000, "zone_three_milli": 600_000,
                                  "zone_four_milli": 0, "zone_five_milli": 0}}},
    {"id": "w-2", "start": "2026-11-24T12:00:00.000Z", "end": "2026-11-24T12:30:00.000Z", "timezone_offset": "-05:00",
     "sport_name": "running", "score_state": "SCORED",
     "score": {"strain": 11.2, "kilojoule": 1255.2, "distance_meter": 5000,
               "zone_durations": {"zone_three_milli": 900_000, "zone_four_milli": 900_000}}},
]


def test_everything_whoop_gives_lands_in_the_day():
    sleeps = [{**SLEEPS[0], "score": {**SLEEPS[0]["score"],
               "stage_summary": {**SLEEPS[0]["score"]["stage_summary"], "total_awake_time_milli": 1_800_000,
                                 "total_in_bed_time_milli": 23_400_000, "sleep_cycle_count": 4, "disturbance_count": 9},
               "sleep_needed": {"baseline_milli": 27_000_000, "need_from_sleep_debt_milli": 3_600_000,
                                "need_from_recent_strain_milli": 1_800_000, "need_from_recent_nap_milli": 0},
               "sleep_consistency_percentage": 77}},
              {**SLEEPS[1], "score": {"stage_summary": {"total_light_sleep_time_milli": 1_500_000}}}]
    d = whoop.daily(RECOVERIES, sleeps, CYCLES, WORKOUTS)[DAY]
    assert d["deep_sleep_h"] == 1.5 and d["rem_sleep_h"] == 1.0 and d["light_sleep_h"] == 3.5
    assert d["awake_h"] == 0.5 and d["in_bed_h"] == 6.5 and d["sleep_cycles"] == 4 and d["sleep_disturbances"] == 9
    assert d["sleep_need_h"] == 9.0 and d["sleep_debt_h"] == 1.0 and d["sleep_consistency_pct"] == 77
    assert d["bedtime_h"] == 23.5 and d["wake_h"] == 6.17  # 23:30 the night before, 06:10
    assert d["nap_min"] == 25 and d["avg_hr"] == 70 and d["max_hr"] == 160
    assert d["workout_count"] == 2 and d["workout_min"] == 105 and d["workout_strain"] == 11.2
    assert d["workout_kcal"] == 700 and d["workout_km"] == 5.0
    assert d["hr_zone1_min"] == 30 and d["hr_zone3_min"] == 25 and d["hr_zone4_min"] == 15


def test_a_bedtime_after_midnight_counts_past_24():
    late = [{**SLEEPS[0], "start": "2026-11-24T06:15:00.000Z"}]  # 01:15 local
    assert whoop.daily([], late, [])[DAY]["bedtime_h"] == 25.25


def test_history_once_then_overlap_and_the_raw_archive_keeps_everything(db, setup):
    import gzip

    data = {"/v2/recovery": RECOVERIES, "/v2/activity/sleep": SLEEPS, "/v2/cycle": CYCLES,
            "/v2/activity/workout": WORKOUTS, whoop.BODY_PATH: {"height_meter": 1.8, "max_heart_rate": 191}}
    fake = whoop.FakeWhoop(data)
    with tenancy.as_user("tester"):
        assert whoop.since_for(db, DAY) == DAY - timedelta(days=whoop.BACKFILL_DAYS)  # the link: one year, fast
        since = whoop.since_for(db, DAY, history=True)
        assert since == whoop.HISTORY_START
        days = whoop.fetch(fake, since, DAY)
        assert days[DAY]["hr_max_profile"] == 191
        whoop.store(db, days, since)
        st = prefs.get(db, whoop.STATUS_PREF)
        assert st["history_done"] and st["first"] == DAY.isoformat() and st["days"] == 1
        assert whoop.since_for(db, DAY + timedelta(days=1), history=True) == DAY - timedelta(days=whoop.OVERLAP_DAYS)
        p = tenancy.user_dir() / whoop.ARCHIVE
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
        raw = json.loads(gzip.decompress(p.read_bytes()))
        assert set(raw["workout"]) == {"w-1", "w-2"} and set(raw["recovery"]) == {"10", "9"}
        assert raw["body_measurement"]["height_meter"] == 1.8
        whoop.archive({"workout": [{**WORKOUTS[0], "sport_name": "boxing"}]})  # a newer copy wins, nothing lost
        raw = json.loads(gzip.decompress(p.read_bytes()))
        assert raw["workout"]["w-1"]["sport_name"] == "boxing" and len(raw["sleep"]) == 3


def test_client_waits_out_the_rate_limit(setup):
    hits, slept = [], []

    def api(req: httpx.Request) -> httpx.Response:
        hits.append(1)
        if len(hits) == 1:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json={"records": [{"n": 1}]})

    with tenancy.as_user("tester"):
        common.vault_write("whoop", {"access_token": "t", "refresh_token": "r", "expires_at": int(time.time()) + 3000})
        client = whoop.WhoopClient(httpx.Client(transport=httpx.MockTransport(api)), sleep=slept.append)
        assert client.collection("/v2/cycle", datetime(2026, 11, 1)) == [{"n": 1}]
        assert slept == [7]
        always = whoop.WhoopClient(httpx.Client(transport=httpx.MockTransport(
            lambda r: httpx.Response(429))), sleep=slept.append)
        with pytest.raises(whoop.WhoopError):
            always.collection("/v2/cycle", datetime(2026, 11, 1))
