"""Readiness from a wearable (WHOOP today): the day's recovery turned into a training call.

Green (67 %+) is a day to push, yellow (34–66 %) a day to train as planned, red (under 34 %) a day to ease off.
WHOOP's own bands. It advises; it never rewrites the plan by itself.
"""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import BodyMetric
from ..i18n import _

GREEN, YELLOW = 67, 34
SHORT_SLEEP_H = 6.0


def values(db: Session, on: date) -> dict[str, float]:
    """The wearable's values for one day (kind → value)."""
    return {m.kind: m.value for m in db.scalars(select(BodyMetric).where(BodyMetric.measured_on == on,
                                                                          BodyMetric.source == "whoop"))}


def band(recovery: float) -> str:
    return "green" if recovery >= GREEN else "yellow" if recovery >= YELLOW else "red"


def morning_line(db: Session, today: date) -> str | None:
    """One line for the morning message, or None without today's recovery."""
    v = values(db, today)
    rec = v.get("recovery_pct")
    if rec is None:
        return None
    msg = {
        "green": "🟢 " + _("Recuperación {n} %: buen día para intentar subir cargas.", n=f"{rec:.0f}"),
        "yellow": "🟡 " + _("Recuperación {n} %: entrena según el plan, sin buscar récords.", n=f"{rec:.0f}"),
        "red": "🔴 " + _("Recuperación {n} %: baja ~10 % las cargas o haz una sesión liviana; nada al fallo.",
                        n=f"{rec:.0f}"),
    }[band(rec)]
    sleep = v.get("sleep_hours")
    if sleep is not None and sleep < SHORT_SLEEP_H:
        msg += " " + _("Dormiste {h} h.", h=f"{sleep:.1f}")
    return msg


def ring(db: Session, today: date) -> dict | None:
    """Progress → the readiness ring: today's recovery (or yesterday's, labelled), else None."""
    for d, label in ((today, _("hoy")), (today - timedelta(days=1), _("ayer"))):
        v = values(db, d)
        if "recovery_pct" in v:
            rec = v["recovery_pct"]
            note = label
            if "hrv_ms" in v:
                note += " · " + _("VFC {n} ms", n=f"{v['hrv_ms']:.0f}")
            if "sleep_hours" in v:
                note += " · " + _("{h} h de sueño", h=f"{v['sleep_hours']:.1f}")
            return {"pct": round(rec), "text": f"{rec:.0f} %", "note": note, "band": band(rec)}
    return None
