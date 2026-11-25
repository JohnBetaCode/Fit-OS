"""Handlers de los jobs del scheduler. Cada módulo de dominio aporta su lógica; aquí solo se conectan."""

from __future__ import annotations

import logging

from .core.db import session_scope
from .core.settings import get_settings
from .i18n import _
from .scheduler import job_handler, timer_handler
from .wa.client import get_client
from .wa.outbox import enqueue_text, gym_chat

log = logging.getLogger("fitos.jobs")

FITOS_HOOK = "http://fitos:8000/webhooks/waha"
waha_health: dict = {"status": None, "hook_present": None, "alerted": False}


@job_handler("waha_check")
async def waha_check() -> str:
    info = await get_client().session_info()
    status = info.get("status")
    hooks = ((info.get("config") or {}).get("webhooks")) or []
    present = get_settings().waha_hook_mode == "global" or any(
        str(h.get("url", "")).rstrip("/") == FITOS_HOOK for h in hooks if isinstance(h, dict))
    waha_health.update(status=status, hook_present=present)
    if status == "WORKING" and not present and gym_chat() and not waha_health["alerted"]:
        with session_scope() as db:
            enqueue_text(
                db,
                _("⚠️ Fit-OS ya no recibe tus mensajes: el webhook de la sesión de WhatsApp desapareció.") + " "
                + _("Vuelve a agregarlo: {cmd}.", cmd="`make waha-hook ACTION=add CONFIRM=1`"),
                handler="waha_check",
            )
        waha_health["alerted"] = True
    if present:
        waha_health["alerted"] = False
    return f"{status} hook={_('sí') if present else 'no'}"


@job_handler("session_timeouts")
async def session_timeouts() -> str | None:
    from .wa.session_flow import check_timeouts  # noqa: PLC0415

    return await check_timeouts()


@timer_handler("rest")
async def rest_over(payload: dict) -> None:
    from .wa.session_flow import rest_over as _rest_over  # noqa: PLC0415

    await _rest_over(payload)


@job_handler("weekly_summary")
async def weekly_summary() -> str | None:
    """Runs every day at `weekly_summary_hour`; posts only on Sundays (the week is Monday to Sunday)."""
    from .core.timeutil import local_today  # noqa: PLC0415
    from .training.weekly import report_text, week_report, week_start  # noqa: PLC0415

    today = local_today()
    if today.weekday() != 6:
        return _("no es domingo")
    if not gym_chat():
        return _("sin grupo configurado")
    with session_scope() as db:
        text = report_text(week_report(db, week_start(today)), get_settings().portal_base_url)
        enqueue_text(db, text, handler="weekly")
    return _("enviado")


@job_handler("morning_plan")
async def morning_plan() -> str | None:
    from .wa.session_flow import send_morning_plan  # noqa: PLC0415

    return await send_morning_plan()


@job_handler("day_close")
async def day_close() -> str | None:
    from .training.service import close_day  # noqa: PLC0415

    with session_scope() as db:
        return close_day(db)


@job_handler("renpho_sync")
async def renpho_sync() -> str | None:
    """Pull the person's Renpho weigh-ins (nothing when they haven't linked it)."""
    import asyncio  # noqa: PLC0415

    from .integrations import renpho  # noqa: PLC0415

    if renpho.linked_email() is None:
        return None

    def run() -> str:
        client = renpho.client_for_current_user()
        if client is None:
            return "not linked"
        with session_scope() as db:
            try:
                res = renpho.sync(db, client)
            except renpho.RenphoError as e:
                return f"error: {e}"
        return f"{res.days} days, latest {res.latest}"

    return await asyncio.to_thread(run)
