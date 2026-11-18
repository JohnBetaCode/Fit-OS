"""Outbox de WhatsApp: todo envío pasa por `wa_messages` (direction='out') y lo drena un solo bucle con
pausas, tope por hora y reintentos. Antes de encolar se registra el "eco esperado" para reconocer el
mensaje cuando WAHA lo devuelva como `fromMe` por el webhook.

Each person's messages live in their own database and go to their own group (`accounts.wa_chat_id`). A person
without a group (web only) gets the row stored as `skipped`: nothing is sent. The hourly cap is per WhatsApp
number, so it counts every account's messages."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import time
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core import accounts, tenancy
from ..core.db import session_scope
from ..core.models import WaMessage
from ..core.settings import get_settings
from ..core.timeutil import utcnow
from .client import WahaError, get_client

log = logging.getLogger("fitos.wa.outbox")

ECHO_TTL_S = 60.0
MAX_ATTEMPTS = 3

_expected: dict[str, float] = {}
_wake = asyncio.Event()


def _echo_key(chat_id: str, text: str) -> str:
    norm = " ".join((text or "").split())
    return hashlib.sha256(f"{chat_id}\x00{norm}".encode()).hexdigest()


def expect_echo(chat_id: str, text: str) -> None:
    now = time.monotonic()
    for k, t in list(_expected.items()):
        if now - t > ECHO_TTL_S:
            _expected.pop(k, None)
    _expected[_echo_key(chat_id, text)] = now


def is_expected_echo(chat_id: str, text: str) -> bool:
    t = _expected.get(_echo_key(chat_id, text))
    return t is not None and time.monotonic() - t <= ECHO_TTL_S


def gym_chat() -> str:
    """WhatsApp group of the current person ("" = web only)."""
    u = tenancy.current()
    acc = accounts.get(u) if u else None
    return (acc.wa_chat_id or "") if acc and acc.active else ""


def enqueue_text(db: Session, text: str, chat_id: str | None = None, handler: str | None = None,
                 planned_item_id: int | None = None) -> WaMessage:
    chat = chat_id or gym_chat()
    if chat:
        expect_echo(chat, text)
    msg = WaMessage(direction="out", chat_id=chat, kind="text", body=text, status="queued" if chat else "skipped",
                    handler=handler, planned_item_id=planned_item_id)
    db.add(msg)
    db.flush()
    _wake.set()
    return msg


def enqueue_media(
    db: Session, kind: str, path: str, caption: str, chat_id: str | None = None, handler: str | None = None,
    planned_item_id: int | None = None,
) -> WaMessage:
    chat = chat_id or gym_chat()
    if chat:
        expect_echo(chat, caption)
    msg = WaMessage(
        direction="out", chat_id=chat, kind=kind, body=caption, media={"path": path},
        status="queued" if chat else "skipped", handler=handler, planned_item_id=planned_item_id,
    )
    db.add(msg)
    db.flush()
    _wake.set()
    return msg


def wake() -> None:
    _wake.set()


async def _send_one(msg_id: int) -> None:
    client = get_client()
    with session_scope() as db:
        msg = db.get(WaMessage, msg_id)
        if msg is None or msg.status != "queued":
            return
        chat, kind, body, media = msg.chat_id, msg.kind, msg.body or "", msg.media or {}
    try:
        if not chat:
            raise WahaError("WA_GYM_CHAT_ID vacío")
        if kind == "video" and media.get("path") and Path(media["path"]).exists():
            wa_id = await client.send_video(chat, Path(media["path"]), body)
        elif kind == "image" and media.get("path") and Path(media["path"]).exists():
            wa_id = await client.send_image(chat, Path(media["path"]), body)
        else:
            wa_id = await client.send_text(chat, body)
        with session_scope() as db:
            msg = db.get(WaMessage, msg_id)
            msg.status, msg.sent_at, msg.attempts = "sent", utcnow(), msg.attempts + 1
            if wa_id and db.scalar(select(WaMessage.id).where(WaMessage.wa_id == wa_id)) is None:
                msg.wa_id = wa_id
    except Exception as e:  # noqa: BLE001 — se reintenta y se registra
        log.warning("envío %s falló: %s", msg_id, e)
        with session_scope() as db:
            msg = db.get(WaMessage, msg_id)
            msg.attempts += 1
            msg.error = str(e)[:500]
            if msg.attempts >= MAX_ATTEMPTS:
                msg.status = "failed"
        await asyncio.sleep(min(30, 2 ** msg.attempts))


def _sent_last_hour(db: Session) -> int:
    since = utcnow() - timedelta(hours=1)
    return db.scalar(
        select(func.count()).select_from(WaMessage).where(WaMessage.direction == "out", WaMessage.sent_at >= since)
    ) or 0


def _sent_last_hour_all(names: list[str]) -> int:
    """Messages sent in the last hour by the WhatsApp number, across every account."""
    n = 0
    for name in names:
        with tenancy.as_user(name), session_scope() as db:
            n += _sent_last_hour(db)
    return n


async def _drain_user(cap_left: int) -> int:
    s = get_settings()
    n = 0
    while n < cap_left:
        with session_scope() as db:
            nxt = db.scalar(
                select(WaMessage.id)
                .where(WaMessage.direction == "out", WaMessage.status == "queued")
                .order_by(WaMessage.id)
                .limit(1)
            )
        if nxt is None:
            return n
        await _send_one(nxt)
        n += 1
        if s.wa_send_gap_seconds:
            await asyncio.sleep(s.wa_send_gap_seconds)
    return n


async def drain_once() -> int:
    """Envía todo lo encolado (en orden, persona por persona). Devuelve cuántos procesó. Usado por el bucle
    y por los tests."""
    s = get_settings()
    names = [a.username for a in accounts.wa_accounts()]
    if not names:
        return 0
    cap_left = s.wa_max_per_hour - _sent_last_hour_all(names)
    n = 0
    for name in names:
        if cap_left <= 0:
            log.warning("tope de %s mensajes/hora alcanzado; la outbox espera", s.wa_max_per_hour)
            break
        with tenancy.as_user(name):
            sent = await _drain_user(cap_left)
        n += sent
        cap_left -= sent
    return n


async def run_forever() -> None:
    while True:
        try:
            await drain_once()
        except Exception:  # noqa: BLE001
            log.exception("error drenando la outbox")
        _wake.clear()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(_wake.wait(), timeout=10.0)
