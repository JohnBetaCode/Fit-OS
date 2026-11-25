"""Integrations → Renpho: link the smart scale's account, sync now, unlink. The sync itself is integrations/renpho.py."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..core import prefs
from ..core.db import get_db
from ..i18n import _
from ..integrations import renpho

router = APIRouter()


def _back() -> RedirectResponse:
    return RedirectResponse("/integraciones#renpho", status_code=303)


def _fail(db: Session, msg: str) -> None:
    renpho.record_error(db, msg)


def client_for_current_user():  # a seam for tests
    return renpho.client_for_current_user()


@router.post("/integraciones/renpho")
def renpho_link(email: str = Form(""), password: str = Form(""), db: Session = Depends(get_db)):
    if "@" not in email or not password:
        _fail(db, _("Escribe el correo y la clave de tu cuenta de Renpho."))
        return _back()
    try:
        renpho.save_credentials(email, password)
        days = renpho.fetch(client_for_current_user())  # proves the credentials; no database open meanwhile
    except renpho.RenphoError as e:
        renpho.forget()  # don't keep credentials that don't work
        _fail(db, str(e))
        return _back()
    renpho.store(db, days)
    return _back()


@router.post("/integraciones/renpho/sincronizar")
def renpho_sync_now(db: Session = Depends(get_db)):
    try:
        client = client_for_current_user()
        days = renpho.fetch(client) if client is not None else None
    except renpho.RenphoError as e:
        _fail(db, str(e))
        return _back()
    if days is not None:
        renpho.store(db, days)
    return _back()


@router.post("/integraciones/renpho/desconectar")
def renpho_unlink(db: Session = Depends(get_db)):
    renpho.forget()  # the weigh-ins already saved stay
    prefs.set_(db, renpho.STATUS_PREF, None)
    return _back()
