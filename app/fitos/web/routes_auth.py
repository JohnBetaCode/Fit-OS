"""Sign in / sign out of the dashboard (accounts in `accounts.sqlite`, see core/sessions.py), and the admin's
"view as" switch (core/auth.py)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..core import accounts, sessions
from ..core.auth import VIEW_COOKIE
from ..core.settings import get_settings
from ..i18n import _
from .ui import render

router = APIRouter()


def _safe_next(nxt: str | None) -> str:
    """Only paths on this site: "/x" yes, "//evil" or "https://…" no."""
    return nxt if nxt and nxt.startswith("/") and not nxt.startswith("//") and "\\\\" not in nxt else "/"


@router.get("/entrar", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    return render(request, "login.html", "", bare=True, next=_safe_next(next), error=None, username="")


@router.post("/entrar", response_class=HTMLResponse)
def login(request: Request, username: str = Form(""), password: str = Form(""), remember: str = Form(""),
          next: str = Form("/")):
    nxt = _safe_next(next)
    wait = sessions.locked()
    if wait:
        msg = _("Demasiados intentos. Espera {n} min.", n=max(1, round(wait / 60)))
        return render(request, "login.html", "", bare=True, next=nxt, error=msg, username=username, status_code=429)
    user = username.strip()
    if not sessions.check_password(user, password):
        sessions.record_failure()
        return render(request, "login.html", "", bare=True, next=nxt, error=_("Usuario o clave incorrectos."),
                      username=user, status_code=401)
    sessions.reset_failures()
    user = user.lower()
    accounts.update(user, last_login_at=datetime.now(UTC).replace(tzinfo=None))
    keep = bool(remember)
    resp = RedirectResponse(nxt, status_code=303)
    resp.set_cookie(sessions.COOKIE, sessions.sign(user, keep), max_age=sessions.REMEMBER_DAYS * 86400 if keep else None,
                    httponly=True, secure=get_settings().app_env != "development", samesite="lax", path="/")
    return resp


@router.post("/salir")
def logout():
    resp = RedirectResponse("/entrar", status_code=303)
    resp.delete_cookie(sessions.COOKIE, path="/")
    resp.delete_cookie(VIEW_COOKIE, path="/")
    return resp


@router.post("/ver-como")
def view_as(request: Request, user: str = Form(""), next: str = Form("/")):
    """Admin only: show another account's data (read-only), or back to your own."""
    acc = getattr(request.state, "account", None)
    resp = RedirectResponse(_safe_next(next), status_code=303)
    if acc is None or not acc.is_admin:
        return resp
    target = accounts.get(user)
    if target is None or target.username == acc.username:
        resp.delete_cookie(VIEW_COOKIE, path="/")
    else:
        resp.set_cookie(VIEW_COOKIE, target.username, httponly=True, samesite="lax", path="/",
                        secure=get_settings().app_env != "development")
    return resp
