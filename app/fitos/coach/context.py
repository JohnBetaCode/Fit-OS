"""Everything a coach needs to plan a day, as one JSON-able dict (`fitos context`).

Used by the plan-day skill (.claude/skills/plan-day) and anyone planning by hand: profile and goals, equipment per
place, program and its next day, recovery per muscle, this week's hard sets, recent sessions set by set, the best
and latest performance per exercise, plans already written, and today's workout as it goes (`live`, used by the
session-status skill). It reads; it never changes anything.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..body import service as body
from ..core import prefs
from ..core.models import PlannedSession, WorkoutSession, WorkoutSet
from ..core.timeutil import local_today
from ..training import metrics, recovery
from ..training import service as tr


def _set(s: WorkoutSet) -> dict[str, Any]:
    out: dict[str, Any] = {"reps": s.reps, "kg": s.weight_kg, "rir": s.rir}
    if s.duration_s:
        out = {"min": round(s.duration_s / 60, 1)}
    if s.pr_flags:
        out["pr"] = list(s.pr_flags)
    return out


def _exercise_row(row: dict) -> dict[str, Any]:
    """One line per exercise ever done: latest top set, best score (e1RM for loads, reps or seconds otherwise)."""
    def iso(d):
        return d.isoformat() if d else None

    return {"id": row["id"], "name": row["name"], "muscle": row["muscle"], "equipment": row["equipment"],
            "kind": row["kind"], "sessions": row["sessions"], "last_date": iso(row["last_date"]),
            "last_top": _set(row["last_top"]) if row["last_top"] is not None else None, "best": row["best"],
            "best_date": iso(row["best_date"]), "max_kg": row["max_weight"], "change_pct": row["change_pct"]}


def _sessions(db: Session, since: date) -> list[dict]:
    rows = db.scalars(select(WorkoutSession).where(WorkoutSession.date >= since, WorkoutSession.state == "completed")
                      .order_by(WorkoutSession.date.desc(), WorkoutSession.id.desc())).all()
    out = []
    for ws in rows:
        by_ex: dict[str, list[WorkoutSet]] = defaultdict(list)
        for s in sorted(ws.sets, key=lambda s: s.id):
            if not s.is_warmup:
                by_ex[s.exercise_id].append(s)
        out.append({
            "date": ws.date.isoformat(), "focus": ws.planned.focus_label if ws.planned else None, "rpe": ws.session_rpe,
            "notes": ws.notes,
            "exercises": [{"id": ex_id, "name": sets[0].exercise.name_es if sets[0].exercise else ex_id,
                           "muscle": sets[0].exercise.primary_muscle if sets[0].exercise else None,
                           "equipment": sets[0].exercise.equipment_tag if sets[0].exercise else None,
                           "sets": [_set(s) for s in sets]} for ex_id, sets in by_ex.items()],
        })
    return out


def _live(db: Session, today: date) -> dict[str, Any] | None:
    """The workout in progress, else today's latest one: every planned item with its status and sets so far."""
    ws = tr.active_session(db) or db.scalar(
        select(WorkoutSession).where(WorkoutSession.date == today).order_by(WorkoutSession.id.desc()).limit(1))
    if ws is None:
        return None
    cur = tr.current_item(db, ws) if ws.state != "completed" else None
    items, sets_by_muscle, volume, cardio_min = [], defaultdict(int), 0.0, 0.0
    for it in (ws.planned.items if ws.planned else []):
        done = tr.item_sets(ws, it)
        for s in done:
            if s.duration_s and not s.reps:
                cardio_min += s.duration_s / 60
                continue
            sets_by_muscle[it.exercise.primary_muscle or "other"] += 1
            volume += (s.reps or 0) * (s.weight_kg or 0)
        items.append({"id": it.exercise_id, "name": it.exercise.name_es, "muscle": it.exercise.primary_muscle,
                      "status": it.status, "current": cur is not None and cur.id == it.id,
                      "planned": {"sets": it.sets, "reps": [it.rep_min, it.rep_max], "kg": it.target_weight_kg},
                      "sets": [_set(s) for s in done]})
    end = ws.ended_at or ws.last_activity_at
    return {"date": ws.date.isoformat(), "state": ws.state, "focus": ws.planned.focus_label if ws.planned else None,
            "minutes": round((end - ws.started_at).total_seconds() / 60) if end and ws.started_at else None,
            "notes": ws.notes, "rpe": ws.session_rpe, "items": items,
            "totals": {"work_sets": sum(sets_by_muscle.values()), "sets_by_muscle": dict(sets_by_muscle),
                       "volume_kg": round(volume, 1), "cardio_min": round(cardio_min, 1)}}


def _plans(db: Session, start: date, days: int = 7) -> list[dict]:
    rows = db.scalars(select(PlannedSession).where(PlannedSession.date >= start,
                                                   PlannedSession.date < start + timedelta(days=days),
                                                   PlannedSession.status.notin_(("replaced",)))
                      .order_by(PlannedSession.date)).all()
    return [{"date": p.date.isoformat(), "focus": p.focus_label, "status": p.status, "source": p.source,
             "items": [{"id": it.exercise_id, "name": it.exercise.name_es, "sets": it.sets,
                        "reps": [it.rep_min, it.rep_max], "kg": it.target_weight_kg} for it in p.items]} for p in rows]


def build(db: Session, days: int = 42, on: date | None = None) -> dict[str, Any]:
    today = on or local_today()
    prof = body.ensure_defaults(db)
    prog = tr.active_program(db)
    lo, hi = int(prefs.get(db, "weekly_sets_target_min")), int(prefs.get(db, "weekly_sets_target_max"))
    next_day = prog.days[prog.rotation_idx % len(prog.days)] if prog and prog.days else None
    return {
        "today": today.isoformat(),
        "profile": {
            "sex": prof.sex, "age": body.age(prof), "height_cm": prof.height_cm,
            "weight_kg": (lambda m: m.value if m else None)(body.latest_metric(db)),
            "goal": prof.primary_goal, "goal_notes": prof.goal_notes, "experience": prof.experience,
            "days_per_week": prof.days_per_week, "session_minutes": prof.session_minutes, "schedule": prof.schedule,
            "injuries": [i for i in (prof.injuries or []) if i.get("active", True)],
            "disliked_exercise_ids": prof.disliked_exercise_ids or [], "coach_notes": prof.coach_notes,
        },
        "equipment": [{"id": ep.id, "name": ep.name, "kind": ep.kind, "default": ep.is_default, "tags": ep.tags,
                       "loads": ep.loads} for ep in body.equipment_profiles(db)],
        "program": None if prog is None else {
            "name": prog.name, "id": prog.id,
            "days": [{"id": d.id, "name": d.name, "slots": d.slots} for d in prog.days],
            "next_day": {"id": next_day.id, "name": next_day.name} if next_day else None,
        },
        "recovery": [{"muscle": r.key, "status": r.status, "fatigue": r.fatigue, "ready_in_h": r.ready_in_h,
                      "last_trained": r.last_trained.isoformat() if r.last_trained else None}
                     for r in recovery.recovery(db)],
        "week": {"window": f"{metrics.rolling_start(today).isoformat()}..{today.isoformat()} (last 7 days, moving)",
                 "target_hard_sets": [lo, hi],
                 "hard_sets_by_muscle": metrics.recent_hard_sets_by_muscle(db, today)},
        "exercises": [_exercise_row(row) for row in metrics.exercise_table(db)],
        "sessions": _sessions(db, today - timedelta(days=days)),
        "plans": _plans(db, today),
        "live": _live(db, today),
        "prefs": {k: prefs.get(db, k) for k in ("rest_seconds_compound", "rest_seconds_isolation", "warmup_sets")},
    }
