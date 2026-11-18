"""Rutas web. Solo validan, llaman a servicios y renderizan."""

from __future__ import annotations

import asyncio
import itertools
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.models import WaMessage
from ..core.settings import get_settings
from ..wa.webhook import ingest
from .ui import render

router = APIRouter()


@router.get("/mas", response_class=HTMLResponse)
async def more(request: Request):
    return render(request, "more.html", "mas")


# ── Media (catálogo) ────────────────────────────────────────────────────────


@router.get("/media/{kind}/{name}")
async def media(kind: str, name: str):
    if kind not in ("mp4", "thumbs") or "/" in name or ".." in name:
        raise HTTPException(404)
    path = get_settings().data_dir / "catalog" / kind / name
    if not path.is_file():
        raise HTTPException(404)
    mt = "video/mp4" if kind == "mp4" else "image/jpeg"
    return FileResponse(path, media_type=mt, headers={"Cache-Control": "public, max-age=604800"})


# ── Simulador del grupo (solo desarrollo) ───────────────────────────────────

_dev_ids = itertools.count(1)


def _require_dev() -> None:
    if not get_settings().is_dev:
        raise HTTPException(404)


def _transcript(db: Session, limit: int = 80) -> list[WaMessage]:
    rows = db.scalars(select(WaMessage).order_by(WaMessage.id.desc()).limit(limit)).all()
    return list(reversed(rows))


@router.get("/dev/wa", response_class=HTMLResponse)
async def dev_wa(request: Request, db: Session = Depends(get_db)):
    _require_dev()
    return render(request, "dev_wa.html", "mas", messages=_transcript(db), media_url=media_url)


@router.get("/dev/wa/log", response_class=HTMLResponse)
async def dev_wa_log(request: Request, db: Session = Depends(get_db)):
    _require_dev()
    return render(request, "_dev_wa_log.html", "mas", messages=_transcript(db), media_url=media_url)


@router.post("/dev/wa", response_class=HTMLResponse)
async def dev_wa_send(request: Request, text: str = Form(...), reply_to: str = Form("")):
    _require_dev()
    s = get_settings()
    from ..wa.outbox import gym_chat

    gym = gym_chat() or "120363000000000000@g.us"
    event = {
        "event": "message.any",
        "session": s.waha_session,
        "payload": {"id": f"true_{gym}_DEVIN{next(_dev_ids):06d}_{gym}", "from": gym, "fromMe": True,
                    "source": "app", "body": text},
    }
    if reply_to:
        event["payload"]["replyTo"] = {"id": reply_to, "participant": gym, "body": ""}
    await ingest(event)
    await asyncio.sleep(0.6)
    from ..core.db import session_scope

    with session_scope() as db:
        msgs = _transcript(db)
        return render(request, "_dev_wa_log.html", "mas", messages=msgs, media_url=media_url)


def media_url(m: WaMessage) -> str | None:
    p = (m.media or {}).get("path")
    if not p:
        return None
    path = Path(p)
    return f"/media/{path.parent.name}/{path.name}"


# ── Pestañas aún sin módulo (se incluye al final: los routers de dominio ganan) ──

placeholder_router = APIRouter()


def _make_placeholder(tab_key: str):
    async def _page(request: Request):
        return render(request, "placeholder.html", tab_key)

    return _page


for _key, _label, _href in [
    ("hoy", "Hoy", "/"), ("entrenos", "", "/entrenos"), ("progreso", "", "/progreso"),
    ("ejercicios", "", "/ejercicios"), ("examenes", "", "/examenes"), ("coach", "", "/coach"),
    ("perfil", "", "/perfil"), ("integraciones", "", "/integraciones"), ("ajustes", "", "/ajustes"),
]:
    placeholder_router.add_api_route(_href, _make_placeholder(_key), response_class=HTMLResponse, methods=["GET"])
