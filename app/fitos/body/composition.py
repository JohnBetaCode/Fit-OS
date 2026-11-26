"""Recomposition in kilos: fat mass and lean mass over time, from weight and body-fat %.

A smart scale estimates body fat by impedance: a single day is noisy (water, food, time of day), the trend is
what's reliable. So every point is shown, and the line is a 14-day average of the weigh-ins before it. The
verdict compares the average of the first and the last 14 days of the range (or of the 3 weigh-ins nearest
each end when they're sparse) and says when there are too few weigh-ins to tell.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import BodyMetric
from ..i18n import _

WINDOW_DAYS = 14
MIN_POINTS = 2  # weigh-ins needed in each end window to compare
FALLBACK_POINTS, FALLBACK_SPAN_DAYS = 3, 45  # sparse weigh-ins: the 3 nearest to each end, if close enough
STEADY_KG = 0.3  # changes smaller than this are noise
NUDGE_AFTER_DAYS = 2  # the morning message asks for a weigh-in when the last one is older than this
RANGES = {"7d": 7, "30d": 30, "90d": 90, "1a": 365, "todo": None}


@dataclass
class Point:
    on: date
    weight: float
    fat_kg: float
    lean_kg: float


def points(db: Session, since: date | None = None) -> list[Point]:
    """One point per day with both weight and body fat (a scale's own reading first, then a manual one)."""
    q = select(BodyMetric).where(BodyMetric.kind.in_(("weight_kg", "body_fat_pct")))
    if since:
        q = q.where(BodyMetric.measured_on >= since)
    best: dict[tuple[date, str], BodyMetric] = {}
    for m in db.scalars(q):
        key = (m.measured_on, m.kind)
        if key not in best or (m.source == "renpho" and best[key].source != "renpho"):
            best[key] = m
    out = []
    for (day, kind), m in sorted(best.items()):
        if kind != "weight_kg" or (day, "body_fat_pct") not in best:
            continue
        w, bf = m.value, best[(day, "body_fat_pct")].value
        fat = round(w * bf / 100, 2)
        out.append(Point(day, w, fat, round(w - fat, 2)))
    return out


def smoothed(pts: list[Point], attr: str, days: int = WINDOW_DAYS) -> list[tuple[str, float]]:
    """Each day's value as the average of the weigh-ins in the `days` before it (that one included)."""
    out = []
    for i, p in enumerate(pts):
        win = [getattr(q, attr) for q in pts[: i + 1] if q.on > p.on - timedelta(days=days)]
        out.append((p.on.isoformat(), round(sum(win) / len(win), 2)))
    return out


def _edge(pts: list[Point]) -> list[Point]:
    """The weigh-ins at the start of `pts` to average: those within 14 days of the first one, or when that's too
    few (people don't weigh in every day), the first 3 if they fit in 45 days."""
    first = pts[0].on
    win = [p for p in pts if abs((p.on - first).days) < WINDOW_DAYS]
    if len(win) >= MIN_POINTS:
        return win
    near = pts[:FALLBACK_POINTS]
    return near if abs((near[-1].on - first).days) <= FALLBACK_SPAN_DAYS else win


def _avg(pts: list[Point], attr: str) -> float:
    return sum(getattr(p, attr) for p in pts) / len(pts)


def summary(pts: list[Point]) -> dict:
    """Start vs end of the range (14-day averages): changes in kilos and an honest verdict."""
    if not pts:
        return {"verdict": "none", "label": _("Sin pesajes con % de grasa en este rango."), "n": 0}
    last = pts[-1]
    start, end = _edge(pts), list(reversed(_edge(list(reversed(pts)))))
    out = {"n": len(pts), "first": pts[0].on, "last": last.on, "weight": last.weight, "fat_kg": last.fat_kg,
           "lean_kg": last.lean_kg, "d_weight": None, "d_fat": None, "d_lean": None}
    if len(start) < MIN_POINTS or len(end) < MIN_POINTS or start[-1].on >= end[0].on:
        out |= {"verdict": "few", "label": _("Pocos pesajes para ver una tendencia: pésate al menos 3 veces por "
                                              "semana, en ayunas.")}
        return out
    d_fat, d_lean = _avg(end, "fat_kg") - _avg(start, "fat_kg"), _avg(end, "lean_kg") - _avg(start, "lean_kg")
    out |= {"d_weight": round(_avg(end, "weight") - _avg(start, "weight"), 1), "d_fat": round(d_fat, 1),
            "d_lean": round(d_lean, 1)}
    if d_fat <= -STEADY_KG and d_lean >= -STEADY_KG:
        out |= {"verdict": "recomp", "label": _("Recomponiendo: baja la grasa y la masa magra se mantiene.")}
    elif d_fat <= -STEADY_KG:
        out |= {"verdict": "cut_losing_lean", "label": _("Bajas grasa, pero también masa magra: más proteína "
                                                         "y no recortes el volumen de entrenamiento.")}
    elif d_fat >= STEADY_KG and d_lean >= STEADY_KG:
        out |= {"verdict": "bulk", "label": _("Subes masa magra y grasa a la vez: volumen. Para recomponer, "
                                              "ajusta la comida.")}
    elif d_fat >= STEADY_KG:
        out |= {"verdict": "fat_gain", "label": _("Sube la grasa sin ganar masa magra: el problema está en la "
                                                  "comida, no en el gimnasio.")}
    elif d_lean >= STEADY_KG:
        out |= {"verdict": "lean_gain", "label": _("Sube la masa magra con la grasa estable: buen camino.")}
    else:
        out |= {"verdict": "steady", "label": _("Estable: ni la grasa ni la masa magra se movieron.")}
    return out


def last_weigh_in(db: Session) -> date | None:
    return db.scalar(select(BodyMetric.measured_on).where(BodyMetric.kind == "weight_kg")
                     .order_by(BodyMetric.measured_on.desc()).limit(1))


def weigh_in_nudge(db: Session, today: date) -> str | None:
    """A line for the morning message when the last weigh-in is getting old (only for people who weigh in)."""
    last = last_weigh_in(db)
    if last is None or (today - last).days <= NUDGE_AFTER_DAYS:
        return None
    return "⚖️ " + _("Pésate hoy en ayunas: tu último pesaje fue hace {n} días.", n=(today - last).days)
