"""Admin: accounts (create, new password, enable/disable) and a way to look at each person's data.

Only admins reach `/admin*` (core/auth.py returns 403 to everyone else). Nothing here reads anyone's health
data: it works on `accounts.sqlite` and on the size of each person's directory.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..core import accounts, tenancy
from ..i18n import _
from ..users import create_user
from .ui import render

router = APIRouter()

def _error(code: str, default: str) -> str:
    return {
        "invalid username": _("Usuario inválido: minúsculas, números, _ o -, de 2 a 32 caracteres, empezando por letra."),
        "username taken": _("Ese usuario ya existe."),
        "a database for that username already exists": _("Ya hay datos guardados con ese usuario."),
        "last admin": _("Tiene que quedar al menos un admin activo."),
    }.get(code, default)


def _short_password() -> str:
    return _("La clave debe tener al menos {n} caracteres.", n=accounts.MIN_PASSWORD)


def _has_db(username: str) -> bool:
    return tenancy.db_path(username).is_file()


def _page(request: Request, *, error: str | None = None, ok: str | None = None, form: dict | None = None,
          status_code: int = 200):
    rows = [{"a": a, "has_db": _has_db(a.username)} for a in accounts.all_accounts()]
    return render(request, "admin.html", "usuarios", rows=rows, error=error, ok=ok, form=form or {},
                  min_password=accounts.MIN_PASSWORD, status_code=status_code)


@router.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, ok: str = ""):
    msgs = {"created": _("Usuario creado. Pásale su clave por un canal privado."),
            "password": _("Clave cambiada."), "saved": _("Guardado.")}
    return _page(request, ok=msgs.get(ok))


@router.post("/admin/usuarios", response_class=HTMLResponse)
def admin_create(request: Request, username: str = Form(""), display_name: str = Form(""),
                 password: str = Form(""), role: str = Form("user")):
    form = {"username": username, "display_name": display_name, "role": role}
    if accounts.password_problem(password):
        return _page(request, error=_short_password(), form=form, status_code=400)
    try:
        create_user(username, display_name or username, password, role="admin" if role == "admin" else "user")
    except ValueError as e:
        return _page(request, error=_error(str(e), _("No se pudo crear el usuario.")), form=form, status_code=400)
    return RedirectResponse("/admin?ok=created", status_code=303)


@router.post("/admin/usuarios/{username}/clave", response_class=HTMLResponse)
def admin_password(request: Request, username: str, password: str = Form("")):
    if accounts.get(username) is None:
        return _page(request, error=_("Ese usuario no existe."), status_code=404)
    if accounts.password_problem(password):
        return _page(request, error=_short_password(), status_code=400)
    accounts.set_password(username, password)
    return RedirectResponse("/admin?ok=password", status_code=303)


@router.post("/admin/usuarios/{username}", response_class=HTMLResponse)
def admin_update(request: Request, username: str, display_name: str = Form(""), role: str = Form(""),
                 active: str = Form("")):
    acc = accounts.get(username)
    if acc is None:
        return _page(request, error=_("Ese usuario no existe."), status_code=404)
    me = request.state.account.username
    fields: dict = {"active": bool(active) or username == me}  # you can't lock yourself out
    if display_name.strip():
        fields["display_name"] = display_name.strip()[:80]
    if role in accounts.ROLES:
        fields["role"] = role
    try:
        accounts.update(username, **fields)
    except ValueError as e:
        return _page(request, error=_error(str(e), _("No se pudo guardar.")), status_code=400)
    return RedirectResponse("/admin?ok=saved", status_code=303)
