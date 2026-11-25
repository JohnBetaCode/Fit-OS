"""Renpho smart-scale sync: wire format, one weigh-in per day, idempotent writes, credentials kept encrypted,
and the Profile page to link and unlink it. Renpho itself is never called: httpx MockTransport or FakeRenpho."""

from __future__ import annotations

import json
import stat
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from fitos.core import prefs, tenancy
from fitos.core.db import session_scope
from fitos.core.models import BodyMetric
from fitos.core.settings import get_settings
from fitos.integrations import renpho
from fitos.main import app

DAY1 = [  # two weigh-ins the same morning and one at night: the first counts
    {"id": 1, "localCreatedAt": "2026-11-24 07:10:00", "weight": 82.4, "bodyfat": 21.3, "muscle": 45.1,
     "water": 55.2, "visfat": 9, "bmr": 1795, "fatFreeWeight": 64.8, "bodyage": 31},
    {"id": 2, "localCreatedAt": "2026-11-24 07:12:00", "weight": 82.2, "bodyfat": 21.0},
    {"id": 3, "localCreatedAt": "2026-11-24 21:00:00", "weight": 83.5, "bodyfat": 22.0},
]
DAY2 = [{"id": 4, "timeStamp": 1764055800000, "weight": 82.0, "bodyfat": 0, "water": 140}]  # ms; junk dropped


@pytest.fixture
def linked(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(s, "data_dir", tmp_path)
    monkeypatch.setattr(s, "secrets_key", "k" * 40)
    return tmp_path


def test_wire_format_round_trips():
    body = renpho._encrypt({"pageNum": 1, "tableName": "t"})
    assert set(body) == {"encryptData"} and renpho._decrypt(body["encryptData"]) == {"pageNum": 1, "tableName": "t"}
    assert renpho._encrypt(None) == renpho._encrypt(None)  # empty body is a fixed, padded block


def test_first_weigh_in_of_each_day_and_sane_values_only():
    days = renpho.first_per_day(DAY1 + DAY2)
    d1 = days[date(2026, 11, 24)]
    assert d1["weight_kg"] == 82.4 and d1["body_fat_pct"] == 21.3 and d1["bmr_kcal"] == 1795
    assert d1["lean_mass_kg"] == 64.8 and d1["visceral_fat"] == 9
    [d2] = [d for d in days if d != date(2026, 11, 24)]
    assert days[d2] == {"weight_kg": 82.0}  # body fat 0 and water 140 are not measurements


def test_sync_writes_one_row_per_day_and_metric_and_is_idempotent(db):
    src = renpho.FakeRenpho(DAY1 + DAY2)
    res = renpho.sync(db, src)
    renpho.sync(db, src)
    rows = db.scalars(select(BodyMetric).where(BodyMetric.source == "renpho")).all()
    assert res.days == 2 and res.latest is not None
    assert len([r for r in rows if r.kind == "weight_kg"]) == 2 and len(rows) == 9
    st = prefs.get(db, renpho.STATUS_PREF)
    assert st["days"] == 2 and st["last_error"] is None
    with pytest.raises(renpho.RenphoError):
        renpho.sync(db, renpho.FakeRenpho(error="caído"))
    assert prefs.get(db, renpho.STATUS_PREF)["last_error"] == "caído"
    assert len(db.scalars(select(BodyMetric).where(BodyMetric.source == "renpho")).all()) == 9  # untouched


def _server(pages: list[list[dict]], *, accept: bool = True, want_version: str = "6.6.0"):
    calls: list[tuple[str, dict, dict]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        payload = json.loads(req.content)
        body = renpho._decrypt(payload["encryptData"]) if payload["encryptData"] else None
        path = req.url.path.lstrip("/")
        calls.append((path, body, dict(req.headers)))

        def ok(data):
            return httpx.Response(200, json={"code": 101, "msg": "success",
                                             "data": renpho._encrypt(data)["encryptData"]})

        if path == renpho.ENDPOINTS["login"]:
            if req.headers.get("appVersion") != want_version or req.headers.get("platform") != "android":
                return httpx.Response(200, json={"code": -114, "message": "Decryption failed"})
            if not accept:
                return httpx.Response(200, json={"code": 50005, "msg": "wrong password"})
            return ok({"login": {"token": "tok", "id": 77}})
        if path == renpho.ENDPOINTS["devices"]:
            return ok({"scale": [{"tableName": "measurements_info_5", "count": 0, "userIds": [77]}]})
        if path == renpho.ENDPOINTS["composition"]:
            n = body["pageNum"]
            return ok(pages[n - 1] if n <= len(pages) else [])
        return httpx.Response(404)

    return httpx.Client(base_url=renpho.API, transport=httpx.MockTransport(handler)), calls


def test_client_logs_in_finds_the_table_and_pages_through():
    full = [{"id": i, "weight": 80.0, "localCreatedAt": f"2026-10-{1 + i % 28:02d} 07:00:00"}
            for i in range(renpho.PAGE)]
    http, calls = _server([full, [{"id": 99, "weight": 81.0, "localCreatedAt": "2026-11-20 07:00:00"}]])
    rows = renpho.RenphoClient("a@b.co", "pw", http=http).measurements()
    assert len(rows) == renpho.PAGE + 1
    login = calls[0][1]["login"]
    assert login["email"] == "a@b.co" and login["password"] == "pw"
    page_calls = [c for c in calls if c[0] == renpho.ENDPOINTS["composition"]]
    assert [c[1]["pageNum"] for c in page_calls] == [1, 2]
    assert page_calls[0][1]["tableName"] == "measurements_info_5" and page_calls[0][2]["token"] == "tok"
    http, _ = _server([], accept=False)
    with pytest.raises(renpho.RenphoError):
        renpho.RenphoClient("a@b.co", "bad", http=http).measurements()


def test_login_sends_app_headers_and_tries_the_next_app_version_on_decrypt_failure():
    http, calls = _server([[]], want_version="7.0.0")
    client = renpho.RenphoClient("a@b.co", "pw", http=http)
    client.measurements()
    logins = [c for c in calls if c[0] == renpho.ENDPOINTS["login"]]
    assert [c[2]["appversion"] for c in logins] == ["6.6.0", "7.0.0"] and client.token == "tok"
    assert all(c[2]["platform"] == "android" for c in calls)
    http, _ = _server([[]], want_version="9.9.9")  # none works: the decrypt error is reported, message included
    with pytest.raises(renpho.RenphoError, match="Decryption failed"):
        renpho.RenphoClient("a@b.co", "pw", http=http).login()


def test_credentials_are_encrypted_on_disk(linked):
    with tenancy.as_user("tester"):
        renpho.save_credentials(" yo@correo.com ", "mi-clave-secreta")
        p = linked / "users" / "tester" / "renpho.json"
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
        assert "mi-clave-secreta" not in p.read_text()
        assert renpho.credentials() == ("yo@correo.com", "mi-clave-secreta")
        renpho.forget()
        assert renpho.credentials() is None and renpho.linked_email() is None


def test_integrations_page_links_syncs_and_unlinks(linked, monkeypatch):
    from fitos.web import routes_renpho

    monkeypatch.setattr(routes_renpho, "client_for_current_user", lambda: renpho.FakeRenpho(DAY1))
    with TestClient(app, follow_redirects=False) as c:
        assert "Báscula Renpho" in c.get("/integraciones").text
        r = c.post("/integraciones/renpho", data={"email": "yo@correo.com", "password": "clave"})
        assert r.headers["location"] == "/integraciones#renpho"
        page = c.get("/integraciones").text
        assert "Conectada como yo@correo.com" in page and "1 días con datos" in page
        prog = c.get("/progreso").text
        assert "Composición corporal" in prog and "Metabolismo basal" in prog
        c.post("/integraciones/renpho/desconectar")
        assert "Conectar" in c.get("/integraciones").text
        assert "Báscula Renpho" not in c.get("/perfil").text  # integrations live in Integrations
    with tenancy.as_user("tester"):
        assert renpho.linked_email() is None
    with session_scope() as db:
        assert db.scalar(select(BodyMetric).where(BodyMetric.kind == "weight_kg", BodyMetric.source == "renpho"))


def test_a_rejected_login_keeps_no_credentials(linked, monkeypatch):
    from fitos.web import routes_renpho

    monkeypatch.setattr(routes_renpho, "client_for_current_user",
                        lambda: renpho.FakeRenpho(error="Renpho no aceptó el correo o la clave."))
    with TestClient(app, follow_redirects=False) as c:
        c.post("/integraciones/renpho", data={"email": "yo@correo.com", "password": "mala"})
        page = c.get("/integraciones").text
    assert "Renpho no aceptó el correo o la clave." in page and 'name="password"' in page
    with tenancy.as_user("tester"):
        assert renpho.linked_email() is None


async def test_scheduled_sync_does_nothing_when_not_linked(linked):
    from fitos.jobs import renpho_sync

    with tenancy.as_user("tester"):
        assert await renpho_sync() is None
