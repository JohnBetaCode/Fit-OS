"""`POST /webhooks/waha`: el webhook de sesión que WAHA envía a Fit-OS.

Por aquí pasan TODOS los chats del número personal del usuario. Orden estricto:
1. HMAC-SHA512 del cuerpo crudo (tiempo constante).
2. Filtro de chat ANTES de cualquier log o escritura: lo que no es el grupo de una cuenta de Fit-OS se
   descarta. El grupo decide de quién es el mensaje: se guarda y procesa en la base de esa persona.
3. Solo `fromMe` (el único que escribe es el dueño, desde el número de la sesión).
4. Anti-bucle: `source == "api"`, id ya registrado como saliente, o eco esperado (hash de chat+texto).
5. Idempotencia por `wa_id`.
6. Se guarda `received`, se responde 200 y se procesa en segundo plano, de a uno y en orden.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from collections import Counter
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..core import accounts, tenancy
from ..core.db import session_scope
from ..core.models import WaMessage
from ..core.settings import get_settings
from ..core.timeutil import utcnow
from .client import bare_id
from .outbox import is_expected_echo

log = logging.getLogger("fitos.wa.webhook")

router = APIRouter()
stats: Counter[str] = Counter()
_lock = asyncio.Lock()
session_status: dict = {"status": None, "at": None}


def verify_hmac(raw: bytes, signature: str, algorithm: str = "sha512") -> bool:
    key = get_settings().waha_webhook_hmac_key
    if not key:
        return get_settings().app_env != "production"
    if (algorithm or "sha512").lower() != "sha512" or not signature:
        return False
    expected = hmac.new(key.encode(), raw, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature.strip().lower())


def owner_of(payload: dict) -> accounts.Account | None:
    """The account whose group this message is in. In groups WAHA puts the group in `from` (also for
    `fromMe`); just in case, `to` counts when it is an account's group."""
    for chat in (str(payload.get("from") or ""), str(payload.get("to") or "")):
        acc = accounts.by_chat(chat)
        if acc is not None:
            return acc
    return None


def chat_of(payload: dict) -> str:
    acc = owner_of(payload)
    return acc.wa_chat_id if acc and acc.wa_chat_id else str(payload.get("from") or "")


def _media_meta(payload: dict) -> dict | None:
    """Where WAHA put the downloaded file of an incoming media message (url, mimetype), if anywhere."""
    if not payload.get("hasMedia"):
        return None
    m = payload.get("media") if isinstance(payload.get("media"), dict) else {}
    return {"url": m.get("url"), "mimetype": m.get("mimetype"), "error": m.get("error")}


def classify(payload: dict) -> str:
    """Decide sin tocar la base qué hacer con un `message.any`. Devuelve: foreign | not_owner | own_echo | accept."""
    acc = owner_of(payload)
    if acc is None or not acc.wa_chat_id:
        return "foreign"
    gym = acc.wa_chat_id
    if not payload.get("fromMe"):
        return "not_owner"
    if str(payload.get("source") or "") == "api":
        return "own_echo"
    if is_expected_echo(gym, payload.get("body") or ""):
        return "own_echo"
    return "accept"


async def ingest(event: dict) -> str:
    """Aplica filtro + guardas + idempotencia y encola el procesamiento. Devuelve el resultado (para stats)."""
    etype = event.get("event")
    if etype == "session.status":
        p = event.get("payload") or {}
        session_status.update(status=p.get("status"), at=utcnow().isoformat())
        stats["session_status"] += 1
        return "session_status"
    if etype not in ("message", "message.any"):
        stats["ignored_event"] += 1
        return "ignored_event"
    payload = event.get("payload") or {}
    verdict = classify(payload)
    if verdict == "foreign":
        stats["dropped_foreign"] += 1  # ni log ni base: es un chat personal ajeno a Fit-OS
        return verdict
    if verdict == "not_owner":
        stats["dropped_not_owner"] += 1
        return verdict
    owner = owner_of(payload)
    assert owner is not None  # classify() said it is an account's group
    with tenancy.as_user(owner.username):  # the database, and the processing task, are this person's
        return _store_and_queue(payload, verdict)


def _store_and_queue(payload: dict, verdict: str) -> str:
    wid = bare_id(payload.get("id"))
    with session_scope() as db:
        if verdict == "accept" and wid:
            out = db.scalar(select(WaMessage.id).where(WaMessage.wa_id == wid, WaMessage.direction == "out"))
            if out is not None:
                verdict = "own_echo"
        if verdict == "own_echo":
            stats["own_echo_ignored"] += 1
            return verdict
        if wid and db.scalar(select(WaMessage.id).where(WaMessage.wa_id == wid)) is not None:
            stats["duplicates"] += 1
            return "duplicate"
        quoted = (payload.get("replyTo") or {}).get("id") if isinstance(payload.get("replyTo"), dict) else None
        msg = WaMessage(
            wa_id=wid or None,
            direction="in",
            chat_id=chat_of(payload),
            from_me=True,
            source=str(payload.get("source") or "") or None,
            kind="text" if not payload.get("hasMedia") else "media",
            body=(payload.get("body") or "").strip(),
            media=_media_meta(payload),
            quoted_wa_id=bare_id(quoted) if quoted else None,
            status="received",
        )
        db.add(msg)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            stats["duplicates"] += 1
            return "duplicate"
        msg_id = msg.id
    stats["accepted"] += 1
    asyncio.get_running_loop().create_task(process(msg_id))
    return "accepted"


async def process(msg_id: int) -> None:
    from .router import handle_incoming  # import tardío: router depende de casi todo

    async with _lock:
        try:
            await handle_incoming(msg_id)
            stats["processed"] += 1
        except Exception:  # noqa: BLE001
            log.exception("error procesando el mensaje %s", msg_id)
            stats["errors"] += 1
            with session_scope() as db:
                m = db.get(WaMessage, msg_id)
                if m:
                    m.status = "error"


async def recover_pending() -> None:
    """Al arrancar: reprocesa los `received` de menos de 10 min; los viejos pasan a `stale`. Cada cuenta con
    grupo, en su base."""
    for acc in accounts.wa_accounts():
        with tenancy.as_user(acc.username):
            await _recover_pending_user()


async def _recover_pending_user() -> None:
    cutoff = utcnow() - timedelta(minutes=10)
    with session_scope() as db:
        rows = db.scalars(select(WaMessage).where(WaMessage.direction == "in", WaMessage.status == "received")).all()
        fresh = []
        for m in rows:
            if m.created_at < cutoff:
                m.status = "stale"
            else:
                fresh.append(m.id)
    for mid in fresh:
        await process(mid)


@router.post("/webhooks/waha")
async def waha_webhook(request: Request) -> dict:
    raw = await request.body()
    if not verify_hmac(raw, request.headers.get("x-webhook-hmac", ""), request.headers.get("x-webhook-hmac-algorithm", "sha512")):
        stats["bad_hmac"] += 1
        raise HTTPException(status_code=401, detail="invalid signature")
    try:
        event = json.loads(raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail="bad json") from e
    result = await ingest(event)
    return {"status": result}
