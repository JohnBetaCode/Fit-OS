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
CYCLES = [{"id": 10, "start": "2026-11-24T11:10:00.000Z", "timezone_offset": "-05:00", "score_state": "SCORED",
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
    assert res.written == 11 and res.latest == DAY
    assert whoop.store(db, whoop.fetch(fake, DAY)).written == 0
    assert whoop.since_for(db, DAY + timedelta(days=1)) == DAY - timedelta(days=whoop.OVERLAP_DAYS)
    assert len(db.scalars(select(BodyMetric).where(BodyMetric.source == "whoop")).all()) == 11


def test_readiness_bands_morning_line_and_ring(db):
    today = local_today()
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
