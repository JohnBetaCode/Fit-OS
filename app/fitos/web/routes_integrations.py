"""Integrations page: WhatsApp and Claude status, and linking each person's outside services (Renpho, WHOOP).

The services themselves live in `fitos/integrations/`; this only validates, calls and redirects back here.
Network calls never run with the database open (a slow API must not lock SQLite): read what's needed, commit,
fetch, then write.
"""

from __future__ import annotations

import contextlib
from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..core import prefs
from ..core.db import get_db
from ..core.settings import get_settings
from ..core.timeutil import fmt_date, local_today, to_local
from ..i18n import _
from ..integrations import renpho, whoop
from ..integrations.common import IntegrationError
from ..wa.outbox import gym_chat
from .ui import render

router = APIRouter()


def _back(anchor: str) -> RedirectResponse:
    return RedirectResponse(f"/integraciones#{anchor}", status_code=303)


def _local_stamp(iso: str | None) -> str | None:
    """An ISO UTC timestamp as "24 nov 07:15" in local time."""
    with contextlib.suppress(TypeError, ValueError):
        dt = to_local(datetime.fromisoformat(iso))
        return f"{fmt_date(dt.date())} {dt:%H:%M}"
    return None


def _day(iso: str | None) -> str | None:
    with contextlib.suppress(TypeError, ValueError):
        return fmt_date(date.fromisoformat(iso))
    return None


def _status(db: Session, pref: str) -> dict:
    st = dict(prefs.get(db, pref) or {})
    return {**st, "synced": _local_stamp(st.get("last_sync")), "latest_day": _day(st.get("latest"))}


@router.get("/integraciones", response_class=HTMLResponse)
async def integrations_page(request: Request, db: Session = Depends(get_db)):
    from ..coach.llm import spent_today_usd
    from ..jobs import waha_health

    s = get_settings()
    return render(request, "integrations.html", "integraciones", waha=waha_health, gym_chat=bool(gym_chat()),
                  llm_mode=s.llm_mode, llm_spent=spent_today_usd(),
                  budget=float(prefs.get(db, "llm_daily_budget_usd")),
                  renpho_email=renpho.linked_email(), renpho_status=_status(db, renpho.STATUS_PREF),
                  whoop_ready=whoop.configured(), whoop_linked=whoop.linked(),
                  whoop_status=_status(db, whoop.STATUS_PREF), whoop_redirect=whoop.redirect_uri())


# ── Renpho ──────────────────────────────────────────────────────────────────────────────────────────────
def client_for_current_user():  # a seam for tests
    return renpho.client_for_current_user()


@router.post("/integraciones/renpho")
def renpho_link(email: str = Form(""), password: str = Form(""), db: Session = Depends(get_db)):
    if "@" not in email or not password:
        renpho.record_error(db, _("Escribe el correo y la clave de tu cuenta de Renpho."))
        return _back("renpho")
    try:
        renpho.save_credentials(email, password)
        days = renpho.fetch(client_for_current_user())  # proves the credentials; no database open meanwhile
    except renpho.RenphoError as e:
        renpho.forget()  # don't keep credentials that don't work
        renpho.record_error(db, str(e))
        return _back("renpho")
    renpho.store(db, days)
    return _back("renpho")


@router.post("/integraciones/renpho/sincronizar")
def renpho_sync_now(db: Session = Depends(get_db)):
    try:
        client = client_for_current_user()
        days = renpho.fetch(client) if client is not None else None
    except renpho.RenphoError as e:
        renpho.record_error(db, str(e))
        return _back("renpho")
    if days is not None:
        renpho.store(db, days)
    return _back("renpho")


@router.post("/integraciones/renpho/desconectar")
def renpho_unlink(db: Session = Depends(get_db)):
    renpho.forget()  # the weigh-ins already saved stay
    prefs.set_(db, renpho.STATUS_PREF, None)
    return _back("renpho")


# ── WHOOP ───────────────────────────────────────────────────────────────────────────────────────────────
def whoop_client():  # a seam for tests
    return whoop.client_for_current_user()


def _whoop_sync(db: Session) -> None:
    client = whoop_client()
    if client is None:
        return
    since = whoop.since_for(db, local_today())
    db.commit()  # end the read before the network: a long fetch must not hold SQLite
    try:
        days = whoop.fetch(client, since)
    except IntegrationError as e:
        whoop.record_error(db, str(e))
        return
    whoop.store(db, days)


@router.post("/integraciones/whoop/conectar")
def whoop_connect(db: Session = Depends(get_db)):
    try:
        url = whoop.authorize_url(db)
    except IntegrationError as e:
        whoop.record_error(db, str(e))
        return _back("whoop")
    return RedirectResponse(url, status_code=303)


@router.get("/integraciones/whoop/callback")
def whoop_callback(request: Request, code: str = "", state: str = "", error: str = "",
                   db: Session = Depends(get_db)):
    if getattr(request.state, "read_only", False):  # viewing someone else: never link on their behalf
        return _back("whoop")
    if error:
        prefs.set_(db, whoop.STATE_PREF, None)
        whoop.record_error(db, _("No autorizaste el acceso en WHOOP."))
        return _back("whoop")
    try:
        whoop.finish_link(db, code, state)
    except IntegrationError as e:
        whoop.record_error(db, str(e))
        return _back("whoop")
    _whoop_sync(db)  # the first sync brings the last year
    return _back("whoop")


@router.post("/integraciones/whoop/sincronizar")
def whoop_sync_now(db: Session = Depends(get_db)):
    _whoop_sync(db)
    return _back("whoop")


@router.post("/integraciones/whoop/desconectar")
def whoop_unlink(db: Session = Depends(get_db)):
    whoop.unlink()  # revokes at WHOOP; the days already saved stay
    prefs.set_(db, whoop.STATUS_PREF, None)
    return _back("whoop")
