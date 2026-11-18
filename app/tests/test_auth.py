"""Dashboard access in production: only through the proxy (shared secret) with a signed session from /entrar,
or a listed Tailscale login."""

from __future__ import annotations

import time

import bcrypt
import pytest
from fastapi.testclient import TestClient

from fitos.core import accounts, sessions
from fitos.core.settings import get_settings
from fitos.main import app

SECRET = "s3cr3t-de-prueba"
PROXY = {"X-Fitos-Proxy": SECRET}


@pytest.fixture
def prod(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "app_env", "production")
    monkeypatch.setattr(s, "dev_user_login", "")
    monkeypatch.setattr(s, "trust_tailscale_headers", False)
    monkeypatch.setattr(s, "owner_logins", "")
    monkeypatch.setattr(s, "proxy_secret", SECRET)
    monkeypatch.setattr(s, "peer_net_cidr", "172.21.0.0/16")
    accounts.set_password("tester", "clave-buena")
    sessions.reset_failures()
    yield s
    sessions.reset_failures()


def _client(ip: str = "172.22.0.3") -> TestClient:
    return TestClient(app, client=(ip, 51000), base_url="https://testserver", follow_redirects=False)


def _get(path: str = "/ajustes", ip: str = "172.22.0.3", **headers):
    with _client(ip) as c:
        return c.get(path, headers=headers)


def test_portal_without_session_goes_to_sign_in(prod):
    r = _get("/ajustes?x=1", **PROXY)
    assert r.status_code == 303 and r.headers["location"] == "/entrar?next=%2Fajustes%3Fx%3D1"
    assert _get("/entrar", **PROXY).status_code == 200
    with _client() as c:
        assert c.post("/semana/dia", headers=PROXY, data={"date": "2026-11-17"}).status_code == 401


def test_sign_in_remember_me_and_sign_out(prod):
    with _client() as c:
        r = c.post("/entrar", headers=PROXY, data={"username": "tester", "password": "clave-buena", "remember": "1",
                                                    "next": "/semana"})
        assert r.status_code == 303 and r.headers["location"] == "/semana"
        cookie = r.headers["set-cookie"]
        assert "HttpOnly" in cookie and "Secure" in cookie and "Max-Age=7776000" in cookie and "SameSite=lax" in cookie
        assert c.get("/ajustes", headers=PROXY).status_code == 200
        assert c.get("/ajustes").status_code == 403  # the cookie alone, without the proxy, isn't a way in
        r = c.post("/salir", headers=PROXY)
        assert r.status_code == 303 and c.get("/ajustes", headers=PROXY).status_code == 303


def test_wrong_password_unknown_user_and_lockout(prod):
    with _client() as c:
        for _ in range(sessions.MAX_FAILS):
            assert c.post("/entrar", headers=PROXY, data={"username": "tester", "password": "mala"}).status_code == 401
        r = c.post("/entrar", headers=PROXY, data={"username": "tester", "password": "clave-buena"})
        assert r.status_code == 429 and "Demasiados intentos" in r.text
    sessions.reset_failures()
    with _client() as c:
        assert c.post("/entrar", headers=PROXY, data={"username": "nadie", "password": "x"}).status_code == 401


def test_next_is_only_local(prod):
    with _client() as c:
        for bad in ("https://evil.example", "//evil.example", "evil"):
            r = c.post("/entrar", headers=PROXY, data={"username": "tester", "password": "clave-buena", "next": bad})
            assert r.headers["location"] == "/"


def test_tokens(prod, monkeypatch):
    now = time.time()
    tok = sessions.sign("tester", remember=False, now=now)
    assert sessions.verify(tok, now=now) == "tester"
    assert sessions.verify(tok, now=now + sessions.SESSION_HOURS * 3600 + 1) is None  # expired
    assert sessions.verify(tok[:-1] + ("0" if tok[-1] != "0" else "1")) is None  # tampered
    assert sessions.verify(sessions.sign("ghost", remember=True)) is None  # no such account
    accounts.create("otra", "Otra", password_hash=bcrypt.hashpw(b"x" * 10, bcrypt.gensalt(rounds=4)).decode())
    other = sessions.sign("otra", remember=True, now=now)
    assert sessions.verify(other, now=now) == "otra"
    accounts.update("otra", active=False)
    assert sessions.verify(other, now=now) is None  # disabling an account signs it out
    with pytest.raises(ValueError, match="last admin"):
        accounts.update("tester", active=False)  # someone must always be able to manage accounts
    monkeypatch.setattr(prod, "proxy_secret", "otro")
    assert sessions.verify(tok, now=now) is None  # new secret: every session ends


def test_without_or_with_wrong_secret_is_rejected(prod):
    assert _get().status_code == 403
    assert _get(**{"X-Fitos-Proxy": "otra"}).status_code == 403
    assert _get(**{"X-Fitos-User": "admin", "X-Forwarded-For": "100.64.1.1"}).status_code == 403
    assert _get(**{"Tailscale-User-Login": "anyone@github"}).status_code == 403  # untrusted header


def test_empty_secret_trusts_no_proxy(prod, monkeypatch):
    monkeypatch.setattr(prod, "proxy_secret", "")
    assert _get(**{"X-Fitos-Proxy": ""}).status_code == 403


def test_listed_tailscale_login_when_enabled(prod, monkeypatch):
    monkeypatch.setattr(prod, "trust_tailscale_headers", True)
    monkeypatch.setattr(prod, "owner_logins", "me@github")
    assert _get(**{"Tailscale-User-Login": "me@github"}).status_code == 200
    assert _get(**{"Tailscale-User-Login": "other@github"}).status_code == 403
    assert _get(**{"Tailscale-User-Login": "proxy:admin"}).status_code == 403


def test_peer_net_only_sees_public_paths(prod):
    assert _get(ip="172.21.0.5", **PROXY).status_code == 403
    assert _get("/healthz", ip="172.21.0.5").status_code == 200
