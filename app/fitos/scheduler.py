"""Scheduler propio dentro del mismo proceso (un solo worker de uvicorn).

Cada `TICK_S` segundos: dispara los `timers` vencidos y evalúa los jobs contra `job_runs`, que hace cada
disparo idempotente por `(job, period_key)`. Los horarios viven en `settings` y cambian en caliente.
Nada corre con un reloj inválido (año < 2026: hosts sin RTC, como placas ARM, arrancan en 1970).

Every tick runs once per active account, as that person (`core/tenancy.py`): their timers, their schedule
and their `job_runs` live in their own database. Jobs about the server itself (`waha_check`) run only for the
primary account (the first admin with a WhatsApp group), which is who gets those alerts."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from .core import accounts, prefs, tenancy
from .core.db import MissingDatabase, session_scope
from .core.models import JobRun, Timer
from .core.timeutil import clock_is_valid, local_now, to_local, utcnow

log = logging.getLogger("fitos.scheduler")

TICK_S = 5.0


@dataclass(frozen=True)
class Job:
    name: str
    kind: str  # interval | daily
    minutes: int = 0  # interval
    time_pref: str = ""  # daily: clave de settings con "HH:MM"
    enabled_pref: str = ""  # clave booleana opcional
    server: bool = False  # about the server, not a person: runs only for the primary account


JOBS: list[Job] = [
    Job("waha_check", "interval", minutes=5, server=True),
    Job("session_timeouts", "interval", minutes=1),
    Job("morning_plan", "daily", time_pref="morning_hour", enabled_pref="morning_enabled"),
    Job("day_close", "daily", time_pref="day_close_hour"),
    Job("weekly_summary", "daily", time_pref="weekly_summary_hour", enabled_pref="weekly_summary_enabled"),
    Job("renpho_sync", "interval", minutes=240),  # smart-scale weigh-ins, for whoever linked Renpho
    Job("whoop_sync", "interval", minutes=60),  # recovery, sleep and strain, for whoever linked WHOOP
]

_handlers: dict[str, Callable[[], Awaitable[str | None]]] = {}
_timer_handlers: dict[str, Callable[[dict], Awaitable[None]]] = {}


def job_handler(name: str):
    def deco(fn):
        _handlers[name] = fn
        return fn

    return deco


def timer_handler(kind: str):
    def deco(fn):
        _timer_handlers[kind] = fn
        return fn

    return deco


def _hhmm(s: str | None, default: str = "06:30") -> tuple[int, int]:
    try:
        h, m = (s or default).split(":")
        return int(h), int(m)
    except ValueError:
        h, m = default.split(":")
        return int(h), int(m)


def due_jobs(now_local: datetime, settings: dict, done: set[tuple[str, str]], jobs: list[Job] = JOBS) -> list[tuple[str, str]]:
    """Función pura: qué `(job, period_key)` tocan ahora y no han corrido."""
    if not clock_is_valid(now_local):
        return []
    out = []
    day = now_local.date().isoformat()
    minute_of_day = now_local.hour * 60 + now_local.minute
    for j in jobs:
        if j.enabled_pref and not settings.get(j.enabled_pref, True):
            continue
        if j.kind == "interval":
            key = f"{day}T{minute_of_day // j.minutes}"
        else:
            h, m = _hhmm(settings.get(j.time_pref), "23:30" if j.name == "day_close" else "06:30")
            if minute_of_day < h * 60 + m:
                continue
            key = day
        if (j.name, key) not in done:
            out.append((j.name, key))
    return out


async def tick() -> None:
    now = utcnow()
    if not clock_is_valid(now):
        return
    primary = accounts.primary()
    for name in accounts.active_usernames():
        jobs = JOBS if primary and name == primary.username else [j for j in JOBS if not j.server]
        try:
            with tenancy.as_user(name):
                await _tick_user(now, jobs)
        except MissingDatabase:
            log.warning("cuenta %s sin base; nada que programar", name)


async def _tick_user(now: datetime, jobs: list[Job]) -> None:
    # timers vencidos
    with session_scope() as db:
        due = db.scalars(select(Timer).where(Timer.status == "pending", Timer.due_at <= now).order_by(Timer.due_at)).all()
        timers = [(t.id, t.kind, dict(t.payload or {})) for t in due]
        for t in due:
            t.status = "done"
    for _tid, kind, payload in timers:
        fn = _timer_handlers.get(kind)
        if fn:
            try:
                await fn(payload)
            except Exception:  # noqa: BLE001
                log.exception("timer %s falló", kind)
    # jobs
    with session_scope() as db:
        settings = prefs.all_prefs(db)
        today = local_now().date().isoformat()
        done = {(r.job, r.period_key) for r in db.scalars(select(JobRun).where(JobRun.period_key >= today)).all()}
    for name, key in due_jobs(to_local(now), settings, done, jobs):
        fn = _handlers.get(name)
        with session_scope() as db:
            db.merge(JobRun(job=name, period_key=key, ran_at=now, ok=True, detail="running"))
        ok, detail = True, None
        if fn:
            try:
                detail = await fn()
            except Exception as e:  # noqa: BLE001
                log.exception("job %s falló", name)
                ok, detail = False, str(e)[:500]
        with session_scope() as db:
            db.merge(JobRun(job=name, period_key=key, ran_at=now, ok=ok, detail=detail))


async def run_forever() -> None:
    while True:
        try:
            await tick()
        except Exception:  # noqa: BLE001
            log.exception("tick del scheduler falló")
        await asyncio.sleep(TICK_S)
