"""Days that weren't normal: sick, injured, travelling. One row per day; marking one pauses that day's plan
(a rest day for the rotation), and they show up in the Week tab, the Sunday summary and the export."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import AuditLog, DayLog
from ..core.timeutil import local_today
from ..i18n import _

KINDS = ("sick", "injured", "travel")
KIND_ES = {"sick": "Enfermo", "injured": "Lesionado", "travel": "De viaje"}
KIND_ICON = {"sick": "🤒", "injured": "🩹", "travel": "✈️"}


def label(kind: str) -> str:
    return _(KIND_ES[kind]) if kind in KIND_ES else kind


def mark(db: Session, on: date, kind: str, note: str | None = None, source: str = "wa") -> DayLog:
    from ..training import service as tr

    if kind not in KINDS:
        raise ValueError(kind)
    row = db.scalar(select(DayLog).where(DayLog.day == on))
    if row is None:
        row = DayLog(day=on, kind=kind, source=source)
        db.add(row)
    row.kind, row.note = kind, (note or "").strip()[:500] or row.note
    tr.skip_today(db, on)  # a not-started plan of that day becomes a rest day (the rotation doesn't move)
    db.add(AuditLog(actor="user", tool="mark_day", entity="day_log", entity_id=on.isoformat(),
                    after={"kind": kind, "note": row.note}))
    db.flush()
    return row


def clear(db: Session, on: date) -> bool:
    row = db.scalar(select(DayLog).where(DayLog.day == on))
    if row is None:
        return False
    db.add(AuditLog(actor="user", tool="clear_day", entity="day_log", entity_id=on.isoformat(),
                    before={"kind": row.kind, "note": row.note}))
    db.delete(row)
    return True


def between(db: Session, start: date, end: date) -> dict[date, DayLog]:
    """Days in [start, end]."""
    return {r.day: r for r in db.scalars(select(DayLog).where(DayLog.day >= start, DayLog.day <= end))}


def streak(db: Session, kind: str, on: date | None = None) -> int:
    """Consecutive days of `kind` ending on `on` (today by default)."""
    on = on or local_today()
    n = 0
    while (row := db.scalar(select(DayLog).where(DayLog.day == on - timedelta(days=n)))) and row.kind == kind:
        n += 1
    return n


def recently(db: Session, kind: str = "sick", days: int = 3, on: date | None = None) -> DayLog | None:
    """The latest `kind` day in the `days` before `on` (not counting `on`)."""
    on = on or local_today()
    return db.scalar(select(DayLog).where(DayLog.kind == kind, DayLog.day < on,
                                         DayLog.day >= on - timedelta(days=days)).order_by(DayLog.day.desc()))
