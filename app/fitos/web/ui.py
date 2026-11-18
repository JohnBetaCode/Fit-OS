"""Jinja2 templates and the shared context (tabs, user, i18n helpers)."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from ..body.service import equipment_name
from ..catalog.service import muscle_label, pattern_label
from ..core import timeutil
from ..core.settings import get_settings
from ..i18n import LANG_NAMES, _, lang, pick
from . import TEMPLATES_DIR

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def tab_labels() -> dict[str, str]:
    """Tab names in the current language (built per request: the language can change at runtime)."""
    return {
        "hoy": _("Hoy"), "entrenar": _("Entrenar"), "semana": _("Semana"), "entrenos": _("Entrenos"), "progreso": _("Progreso"), "ejercicios": _("Ejercicios"),
        "examenes": _("Exámenes"), "coach": _("Coach"), "perfil": _("Perfil"),
        "integraciones": _("Integraciones"), "ajustes": _("Ajustes"), "mas": _("Más"), "usuarios": _("Usuarios"),
    }


TAB_HREFS = [
    ("hoy", "/"), ("entrenar", "/entreno"), ("semana", "/semana"), ("entrenos", "/entrenos"), ("progreso", "/progreso"), ("ejercicios", "/ejercicios"),
    ("examenes", "/examenes"), ("coach", "/coach"), ("perfil", "/perfil"), ("integraciones", "/integraciones"),
    ("ajustes", "/ajustes"),
]
MOBILE_TAB_HREFS = [("hoy", "/", "🏠"), ("entrenar", "/entreno", "💪"), ("semana", "/semana", "🗓️"),
                    ("progreso", "/progreso", "📈"), ("mas", "/mas", "☰")]
MORE_KEYS = {"entrenos", "examenes", "ejercicios", "coach", "perfil", "integraciones", "ajustes", "mas", "usuarios"}


def fmt_kg(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:g}"


def fmt_num(v: float | None, decimals: int = 1) -> str:
    if v is None:
        return "—"
    if decimals == 0 or float(v).is_integer():
        whole = f"{v:,.0f}"
        return whole if lang() == "en" else whole.replace(",", ".")
    return f"{v:.{decimals}f}"


templates.env.filters["kg"] = fmt_kg
templates.env.filters["num"] = fmt_num
templates.env.globals.update(
    _=_, pick=pick, lang=lang, LANG_NAMES=LANG_NAMES, fmt_date=timeutil.fmt_date,
    fmt_day_month=timeutil.fmt_day_month, weekday_short=timeutil.weekday_short,
    weekday_long=timeutil.weekday_long, months_short=timeutil.months_short, muscle_label=muscle_label,
    pattern_label=pattern_label, equipment_name=equipment_name,
)


def who(request: Request) -> dict[str, Any]:
    """Signed-in account, the account whose data is shown, and the admin's switcher."""
    from ..core import accounts  # noqa: PLC0415

    acc = getattr(request.state, "account", None)
    viewing = getattr(request.state, "viewing", "")
    out: dict[str, Any] = {"account": acc, "is_admin": bool(acc and acc.is_admin), "viewing": viewing,
                           "read_only": bool(getattr(request.state, "read_only", False)), "viewing_name": "",
                           "people": []}
    if acc and acc.is_admin:
        out["people"] = [(a.username, a.display_name, a.active) for a in accounts.all_accounts()]
        if out["read_only"]:
            other = accounts.get(viewing)
            out["viewing_name"] = other.display_name if other else viewing
    return out


def form_num(v: str | None) -> float | None:
    """A number from a form field that may be empty ("" → None; "22,5" works). Browsers send empty optional
    fields as "", which a `float | None = Form(None)` parameter rejects with a 422."""
    v = (v or "").strip().replace(",", ".")
    if not v:
        return None
    try:
        return float(v)
    except ValueError as e:
        raise ValueError(_("Valores fuera de rango")) from e


def version(acc: Any) -> str:
    """Footer: the short commit for everyone; admins also see the branch it came from."""
    s = get_settings()
    short = (s.git_sha or "dev")[:7]
    if acc is not None and acc.is_admin:
        return f"{s.git_branch} · {short}" if s.git_branch else short
    return short


def render(request: Request, name: str, tab: str, **ctx: Any):
    labels = tab_labels()
    acc = getattr(request.state, "account", None)
    tabs = TAB_HREFS + ([("usuarios", "/admin")] if acc and acc.is_admin else [])
    base = {
        "request": request,
        "tab": tab,
        "tab_label": labels.get(tab, "Fit-OS"),
        "tabs": [(k, labels[k], href) for k, href in tabs],
        "mobile_tabs": [(k, labels[k], href, icon) for k, href, icon in MOBILE_TAB_HREFS],
        "more_keys": MORE_KEYS,
        "user": acc.display_name if acc else getattr(request.state, "user", ""),
        "is_dev": get_settings().is_dev,
        "version": version(acc),
        **who(request),
    }
    status = ctx.pop("status_code", 200)
    base.update(ctx)
    return templates.TemplateResponse(request, name, base, status_code=status)
