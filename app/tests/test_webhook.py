"""Webhook: HMAC, filtro de chats ajenos sin persistir, fromMe, anti-bucle y duplicados."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from fitos.core.db import session_scope
from fitos.core.models import WaMessage
from fitos.main import app
from fitos.wa import outbox, webhook

GYM = "120363999999999999@g.us"
OTHER = "573001112233@c.us"


def sign(body: bytes, key: str = "test-key") -> str:
    return hmac.new(key.encode(), body, hashlib.sha512).hexdigest()


def event(body: str, chat: str = GYM, from_me: bool = True, source: str = "app", mid: str = "ABC1") -> dict:
    return {"event": "message.any", "session": "default",
            "payload": {"id": f"true_{chat}_{mid}_x@lid", "from": chat, "fromMe": from_me, "source": source, "body": body}}


def post(client: TestClient, ev: dict, key: str = "test-key"):
    raw = json.dumps(ev).encode()
    return client.post("/webhooks/waha", content=raw, headers={"X-Webhook-Hmac": sign(raw, key), "X-Webhook-Hmac-Algorithm": "sha512"})


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def count(**where) -> int:
    with session_scope() as db:
        q = select(func.count()).select_from(WaMessage)
        for k, v in where.items():
            q = q.where(getattr(WaMessage, k) == v)
        return db.scalar(q)


def test_bad_hmac_rejected(client):
    r = post(client, event("ping"), key="wrong")
    assert r.status_code == 401
    assert count() == 0


def test_foreign_chat_dropped_without_persisting_or_logging(client, caplog):
    caplog.set_level(logging.DEBUG)
    r = post(client, event("mi secreto personal", chat=OTHER))
    assert r.json()["status"] == "foreign"
    assert count() == 0
    assert "mi secreto personal" not in caplog.text


def test_not_from_me_ignored(client):
    r = post(client, event("hola", from_me=False))
    assert r.json()["status"] == "not_owner"
    assert count() == 0


def test_api_source_is_own_echo(client):
    assert post(client, event("pong", source="api")).json()["status"] == "own_echo"
    assert count() == 0


def test_expected_echo_guard(client):
    outbox.expect_echo(GYM, "Hola  desde   Fit-OS")
    assert post(client, event("Hola desde Fit-OS", source="app", mid="E1")).json()["status"] == "own_echo"


def test_sent_id_guard(client):
    with session_scope() as db:
        db.add(WaMessage(direction="out", chat_id=GYM, wa_id="SENT9", body="x", status="sent"))
    assert post(client, event("x2", mid="SENT9")).json()["status"] == "own_echo"


def test_duplicate_and_ping_roundtrip(client, fake_wa):
    assert post(client, event("Ping", mid="P1")).json()["status"] == "accepted"
    assert post(client, event("Ping", mid="P1")).json()["status"] == "duplicate"

    async def settle():
        for _ in range(50):
            await asyncio.sleep(0.02)
            if count(direction="in", status="processed"):
                break
        await outbox.drain_once()

    client.portal.call(settle)  # en el loop del TestClient, donde corre el procesamiento
    assert count(direction="in", status="processed") == 1
    assert any(s.text.startswith("pong") for s in fake_wa.sent)
    # el eco del pong vuelve como fromMe con source=api → ignorado
    assert post(client, event(fake_wa.sent[-1].text, source="api", mid=fake_wa.sent[-1].wa_id)).json()["status"] == "own_echo"


def test_stats_counters(client):
    webhook.stats.clear()
    post(client, event("a", chat=OTHER))
    assert webhook.stats["dropped_foreign"] == 1


def test_healthz_public_and_pages_need_identity(client):
    assert client.get("/healthz").json()["ok"] is True
    assert client.get("/").status_code == 200  # DEV_USER_LOGIN en tests
