"""Body map numbers: how recovered each muscle is, and how much it got this week.

Recovery is an estimate from your own hard sets, not a measurement. Every hard set (RIR ≤ 4 or no RIR, no
warm-ups) adds fatigue to its muscles (1.0 to the primary, 0.5 to each secondary) that fades exponentially:
large muscles take longer (τ = 60 h) than small ones (τ = 40 h). With these constants ten hard sets of a large
muscle are "fatigued" (red) right after the workout, "recovering" (orange) about a day later and "ready"
(green) after ~72 h. It is a heuristic (not validated), meant to roughly match the common advice of two to
three days between hard sessions of the same muscle; tune RED, ORANGE and TAU_H if it disagrees with how you
feel.

Week coverage counts the same hard sets as the Today card (`metrics.weekly_hard_sets_by_muscle`) against the
weekly target in Settings (10–20 by default).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..catalog.service import MUSCLE_ES
from ..core.models import WorkoutSession, WorkoutSet
from ..core.timeutil import local_today, utcnow
from .metrics import _is_hard

LARGE = frozenset({"chest", "lats", "upper_back", "lower_back", "quads", "hamstrings", "glutes"})
TAU_H = {True: 60.0, False: 40.0}  # large muscle?
RED, ORANGE = 7.0, 3.0  # fatigue (≈ recent hard sets) at which a muscle is "fatigued" / "recovering"
LOOKBACK_DAYS = 10  # older sets are below 1 % of their fatigue
HISTORY_DAYS = 120  # how far back "last trained" looks


@dataclass
class MuscleRecovery:
    key: str
    fatigue: float
    status: str  # ready | recovering (orange) | fatigued (red)
    last_trained: date | None
    ready_in_h: float  # hours until green (0 when ready)

    @property
    def days_since(self) -> int | None:
        return (local_today() - self.last_trained).days if self.last_trained else None


def tau(muscle: str) -> float:
    return TAU_H[muscle in LARGE]


def status_for(fatigue: float) -> str:
    return "fatigued" if fatigue >= RED else "recovering" if fatigue >= ORANGE else "ready"


def hours_to_ready(fatigue: float, muscle: str) -> float:
    """Hours until the fatigue drops below the orange line."""
    return 0.0 if fatigue < ORANGE else tau(muscle) * math.log(fatigue / ORANGE)


def _when(ws: WorkoutSession) -> datetime:
    """When the session happened (its start; a session logged without times counts at local noon)."""
    if ws.started_at is not None:
        return ws.started_at if ws.started_at.tzinfo else ws.started_at.replace(tzinfo=UTC)
    return datetime.combine(ws.date, time(12)).replace(tzinfo=UTC)


def _contribs(s: WorkoutSet) -> list[tuple[str, float]]:
    ex = s.exercise
    if ex is None:
        return []
    out = [(ex.primary_muscle, 1.0)] if ex.primary_muscle else []
    out += [(m, 0.5) for m in (ex.secondary_muscles or []) if m and m != ex.primary_muscle]
    return out


def recovery(db: Session, now: datetime | None = None) -> list[MuscleRecovery]:
    """Every muscle in the catalog, most fatigued first."""
    now = now or utcnow()
    today = local_today()
    fatigue: dict[str, float] = defaultdict(float)
    last: dict[str, date] = {}
    rows = db.execute(
        select(WorkoutSet, WorkoutSession).join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(WorkoutSession.date >= today - timedelta(days=HISTORY_DAYS))
    ).all()
    for s, ws in rows:
        if not _is_hard(s):
            continue
        hours = max(0.0, (now - _when(ws)).total_seconds() / 3600)
        for m, w in _contribs(s):
            if ws.date > last.get(m, date.min):
                last[m] = ws.date
            if ws.date >= today - timedelta(days=LOOKBACK_DAYS):
                fatigue[m] += w * math.exp(-hours / tau(m))
    out = [MuscleRecovery(k, round(fatigue.get(k, 0.0), 2), status_for(fatigue.get(k, 0.0)), last.get(k),
                          round(hours_to_ready(fatigue.get(k, 0.0), k), 1)) for k in MUSCLE_ES]
    out.sort(key=lambda r: (-r.fatigue, r.key))
    return out


def week_levels(sets_by_muscle: dict[str, float], lo: int, hi: int) -> dict[str, str]:
    """none | low | mid | target | over for every muscle: what's missing this week at a glance."""
    out = {}
    for k in MUSCLE_ES:
        v = float(sets_by_muscle.get(k, 0.0))
        out[k] = ("none" if v <= 0 else "low" if v < lo / 2 else "mid" if v < lo else "target" if v <= hi
                  else "over")
    return out


# Focus groups (training/assist.py GROUPS) → whether they're good to train today
def ready_groups(rows: list[MuscleRecovery], groups: dict[str, list[str]]) -> list[str]:
    """Groups whose main (first) muscle is green, none in the red and most green; freshest first ("full" never
    suggested)."""
    by = {r.key: r for r in rows}
    ok = []
    for g, muscles in groups.items():
        if g == "full":
            continue
        rs = [by[m] for m in muscles if m in by]
        if (rs and rs[0].status == "ready" and all(r.status != "fatigued" for r in rs)
                and sum(r.status == "ready" for r in rs) * 2 >= len(rs)):
            ok.append((sum(r.fatigue for r in rs) / len(rs), g))
    return [g for _f, g in sorted(ok)]
