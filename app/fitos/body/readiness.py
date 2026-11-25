"""Readiness from a wearable: the day's recovery turned into a training call, plus an early illness warning.

Green (67 %+) is a day to push, yellow (34–66 %) a day to train as planned, red (under 34 %) a day to ease off.
It advises; it never rewrites the plan by itself. Everything here is None unless a wearable is active
(`integrations/wearables.py`): linked, syncing without errors and recent.

Illness: two or more of skin temperature, resting HR and respiratory rate up, or HRV down, by 2+ standard
deviations against your own previous 30 days. On a year of real data that marked the days around being sick
(and some heavy nights out): a reason to go easy, never a diagnosis.
"""

from __future__ import annotations

import statistics
from bisect import bisect_left
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import BodyMetric
from ..i18n import _

GREEN, YELLOW = 67, 34
SHORT_SLEEP_H = 6.0
ILLNESS_SIGNALS = (("skin_temp_c", 1), ("resting_hr", 1), ("respiratory_rate", 1), ("hrv_ms", -1))
ILLNESS_Z = 2.0
ILLNESS_BASE_DAYS = 30
ILLNESS_MIN_BASE = 14


def source(db: Session) -> str | None:
    from ..integrations.wearables import active_source  # noqa: PLC0415

    return active_source(db)


def values(db: Session, on: date, src: str | None = None) -> dict[str, float]:
    """The active wearable's values for one day (kind → value); {} without one."""
    src = src or source(db)
    if src is None:
        return {}
    return {m.kind: m.value for m in db.scalars(select(BodyMetric).where(BodyMetric.measured_on == on,
                                                                          BodyMetric.source == src))}


def band(recovery: float) -> str:
    return "green" if recovery >= GREEN else "yellow" if recovery >= YELLOW else "red"


def _signal_label(kind: str) -> str:
    return {"skin_temp_c": _("temperatura de la piel alta"), "resting_hr": _("pulso en reposo alto"),
            "respiratory_rate": _("respiración acelerada"), "hrv_ms": _("VFC baja")}[kind]


def illness_flags(rows: dict[date, dict[str, float]], days: list[date] | None = None) -> dict[date, list[str]]:
    """Day → the signals out of your normal range (kinds), for the days given (all by default)."""
    ordered = sorted(rows)
    out: dict[date, list[str]] = {}
    for d in days if days is not None else ordered:
        today = rows.get(d) or {}
        base = [rows[x] for x in ordered[bisect_left(ordered, d - timedelta(days=ILLNESS_BASE_DAYS)):
                                         bisect_left(ordered, d)]]
        hits = []
        for kind, sign in ILLNESS_SIGNALS:
            vals = [b[kind] for b in base if kind in b]
            if kind not in today or len(vals) < ILLNESS_MIN_BASE:
                continue
            sd = statistics.pstdev(vals)
            if sd > 0 and (today[kind] - statistics.mean(vals)) / sd * sign >= ILLNESS_Z:
                hits.append(kind)
        if len(hits) >= 2:
            out[d] = hits
    return out


def rows_between(db: Session, src: str, start: date, end: date) -> dict[date, dict[str, float]]:
    rows: dict[date, dict[str, float]] = {}
    for m in db.scalars(select(BodyMetric).where(BodyMetric.source == src, BodyMetric.measured_on >= start,
                                                 BodyMetric.measured_on <= end)):
        rows.setdefault(m.measured_on, {})[m.kind] = m.value
    return rows


def illness(db: Session, today: date, src: str | None = None) -> list[str] | None:
    """Today's out-of-range signals (labels) when there are two or more, else None."""
    src = src or source(db)
    if src is None:
        return None
    rows = rows_between(db, src, today - timedelta(days=ILLNESS_BASE_DAYS), today)
    hits = illness_flags(rows, [today]).get(today)
    return [_signal_label(k) for k in hits] if hits else None


def illness_line(db: Session, today: date, src: str | None = None) -> str | None:
    signals = illness(db, today, src)
    if not signals:
        return None
    return "🤒 " + _("Tus señales de hoy están fuera de lo normal ({signals}): puede ser el inicio de una "
                     "enfermedad, alcohol o una mala noche. Hoy suave, nada al fallo.", signals=", ".join(signals))


def morning_line(db: Session, today: date) -> str | None:
    """The morning message's readiness (and illness warning), or None without an active wearable or data."""
    src = source(db)
    if src is None:
        return None
    v = values(db, today, src)
    rec = v.get("recovery_pct")
    sick = illness_line(db, today, src)
    if rec is None:
        return sick
    msg = {
        "green": "🟢 " + _("Recuperación {n} %: buen día para intentar subir cargas.", n=f"{rec:.0f}"),
        "yellow": "🟡 " + _("Recuperación {n} %: entrena según el plan, sin buscar récords.", n=f"{rec:.0f}"),
        "red": "🔴 " + _("Recuperación {n} %: baja ~10 % las cargas o haz una sesión liviana; nada al fallo.",
                        n=f"{rec:.0f}"),
    }[band(rec)]
    sleep = v.get("sleep_hours")
    if sleep is not None and sleep < SHORT_SLEEP_H:
        msg += " " + _("Dormiste {h} h.", h=f"{sleep:.1f}")
    return f"{sick}\n{msg}" if sick else msg


def ring(db: Session, today: date) -> dict | None:
    """Progress → the readiness ring: today's recovery (or yesterday's, labelled), else None."""
    src = source(db)
    if src is None:
        return None
    for d, label in ((today, _("hoy")), (today - timedelta(days=1), _("ayer"))):
        v = values(db, d, src)
        if "recovery_pct" in v:
            rec = v["recovery_pct"]
            note = label
            if "hrv_ms" in v:
                note += " · " + _("VFC {n} ms", n=f"{v['hrv_ms']:.0f}")
            if "sleep_hours" in v:
                note += " · " + _("{h} h de sueño", h=f"{v['sleep_hours']:.1f}")
            return {"pct": round(rec), "text": f"{rec:.0f} %", "note": note, "band": band(rec)}
    return None
