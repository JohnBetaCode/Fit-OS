"""Medals: your gold, silver and bronze on each exercise.

The rubric, so it can be checked:
- Each workout competes with its best set; the podium is your three best days, so one great day can't take all
  three medals.
- Loaded lifts are ranked by estimated 1RM (Epley with RIR: weight × (1 + (reps + RIR) / 30)), which puts
  60 × 10 and 70 × 5 on one scale. When a set has too many reps for Epley (reps + RIR over 12) the exercise is
  ranked by weight, then reps.
- Bodyweight exercises are ranked by reps. Cardio and timed work get no medals.
- A tie goes to the earlier day: you got there first.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import Exercise, WorkoutSession, WorkoutSet
from .progression import e1rm

MEDALS = ("🥇", "🥈", "🥉")


@dataclass
class Medal:
    rank: int  # 1 gold, 2 silver, 3 bronze
    exercise_id: str
    on: date
    session_id: int
    set_id: int
    weight_kg: float | None
    reps: int | None
    score: float
    kind: str  # e1rm | weight | reps

    @property
    def icon(self) -> str:
        return MEDALS[self.rank - 1]


def _e1rm(s: WorkoutSet) -> float | None:
    return s.e1rm_kg if s.e1rm_kg is not None else e1rm(s.weight_kg, s.reps, s.rir)


def _kind(sets: list[WorkoutSet]) -> str | None:
    if all(s.duration_s and not s.reps for s in sets):
        return None  # timed / cardio
    loaded = [s for s in sets if s.weight_kg and s.reps]
    if not loaded:
        return "reps"
    return "e1rm" if all(_e1rm(s) is not None for s in loaded) else "weight"


def _key(s: WorkoutSet, kind: str) -> tuple[float, ...]:
    if kind == "e1rm":
        return ((_e1rm(s) or 0.0),)
    if kind == "weight":
        return (s.weight_kg or 0.0, float(s.reps or 0))
    return (float(s.reps or 0),)


def _sets_by_exercise(db: Session, exercise_ids: list[str] | None) -> dict[str, list[tuple[WorkoutSet, date]]]:
    q = (select(WorkoutSet, WorkoutSession.date).join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
         .where(WorkoutSet.is_warmup.is_(False)))
    if exercise_ids is not None:
        q = q.where(WorkoutSet.exercise_id.in_(exercise_ids))
    out: dict[str, list[tuple[WorkoutSet, date]]] = defaultdict(list)
    for s, d in db.execute(q):
        out[s.exercise_id].append((s, d))
    return out


def _podium(rows: list[tuple[WorkoutSet, date]]) -> list[Medal]:
    sets = [s for s, _d in rows if s.reps or s.duration_s]
    kind = _kind(sets) if sets else None
    if kind is None:
        return []
    usable = [(s, d) for s, d in rows if s.reps and (kind == "reps" or s.weight_kg)]
    best: dict[int, tuple[WorkoutSet, date]] = {}  # session → its best set
    for s, d in usable:
        cur = best.get(s.session_id)
        if cur is None or _key(s, kind) > _key(cur[0], kind):
            best[s.session_id] = (s, d)
    ranked = sorted(best.values(), key=lambda sd: (tuple(-x for x in _key(sd[0], kind)), sd[1], sd[0].id))
    return [Medal(i + 1, s.exercise_id, d, s.session_id, s.id, s.weight_kg, s.reps, _key(s, kind)[0], kind)
            for i, (s, d) in enumerate(ranked[:3])]


def podium(db: Session, exercise_id: str) -> list[Medal]:
    """Gold, silver and bronze for one exercise (fewer when there are fewer workouts)."""
    return _podium(_sets_by_exercise(db, [exercise_id]).get(exercise_id, []))


def set_medals(db: Session, exercise_ids: list[str]) -> dict[int, Medal]:
    """Set id → its medal, for the sets that hold a podium place on these exercises."""
    out: dict[int, Medal] = {}
    for rows in _sets_by_exercise(db, list(set(exercise_ids))).values():
        for m in _podium(rows):
            out[m.set_id] = m
    return out


def recent(db: Session, since: date) -> list[tuple[Medal, Exercise]]:
    """Medals you hold that were won on or after `since`, newest first (gold before silver on the same day)."""
    out = []
    for ex_id, rows in _sets_by_exercise(db, None).items():
        for m in _podium(rows):
            if m.on >= since:
                ex = db.get(Exercise, ex_id)
                if ex is not None:
                    out.append((m, ex))
    return sorted(out, key=lambda me: (-me[0].on.toordinal(), me[0].rank))
