"""Métricas de la Fase 1 (design §10), calculadas al vuelo desde `workout_sets`."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.models import Profile, WorkoutSession, WorkoutSet
from ..core.timeutil import local_today
from .progression import e1rm

HARD_SET_MAX_RIR = 4


def _load_kind(s, override: str | None) -> str | None:
    if override:
        return override
    ex = getattr(s, "exercise", None)
    return getattr(ex, "load_kind", None) or getattr(s, "load_kind", None)


def tonnage(sets: Iterable, load_kind: str | None = None) -> float:
    """Σ peso × reps sin calentamiento; `per_hand` × 2. Acepta `WorkoutSet` (usa `exercise.load_kind`) u
    objetos con `reps`, `weight_kg` y opcionalmente `load_kind`/`is_warmup`."""
    total = 0.0
    for s in sets:
        if getattr(s, "is_warmup", False) or not s.reps or not s.weight_kg:
            continue
        mult = 2 if _load_kind(s, load_kind) == "per_hand" else 1
        total += s.weight_kg * s.reps * mult
    return round(total, 1)


def _is_hard(s: WorkoutSet) -> bool:
    return not s.is_warmup and bool(s.reps or s.duration_s) and (s.rir is None or s.rir <= HARD_SET_MAX_RIR)


def _sets_between(db: Session, start: date, end: date) -> list[tuple[WorkoutSet, date]]:
    return list(db.execute(
        select(WorkoutSet, WorkoutSession.date).join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(WorkoutSession.date >= start, WorkoutSession.date < end)
    ).all())


def weekly_hard_sets_by_muscle(db: Session, week_start: date) -> dict[str, float]:
    """Sets duros (RIR ≤ 4 o sin RIR) de la semana [week_start, +7 d): 1,0 al principal, 0,5 a cada secundario."""
    out: dict[str, float] = defaultdict(float)
    for s, _ in _sets_between(db, week_start, week_start + timedelta(days=7)):
        if not _is_hard(s) or s.exercise is None:
            continue
        if s.exercise.primary_muscle:
            out[s.exercise.primary_muscle] += 1.0
        for m in s.exercise.secondary_muscles or []:
            if m != s.exercise.primary_muscle:
                out[m] += 0.5
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _set_e1rm(s: WorkoutSet) -> float | None:
    return s.e1rm_kg if s.e1rm_kg is not None else e1rm(s.weight_kg, s.reps, s.rir)


def e1rm_series(db: Session, exercise_id: str) -> list[tuple[date, float]]:
    """Mejor e1RM por día para un ejercicio, en orden cronológico."""
    best: dict[date, float] = {}
    rows = db.execute(
        select(WorkoutSet, WorkoutSession.date).join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(WorkoutSet.exercise_id == exercise_id, WorkoutSet.is_warmup.is_(False))
    ).all()
    for s, d in rows:
        if (v := _set_e1rm(s)) is not None and v > best.get(d, 0):
            best[d] = v
    return sorted(best.items())


def recent_prs(db: Session, limit: int = 10) -> list[dict]:
    """Últimos sets con banderas de PR (`pr_flags`), el más reciente primero."""
    rows = db.execute(
        select(WorkoutSet, WorkoutSession.date).join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(func.json_array_length(WorkoutSet.pr_flags) > 0)
        .order_by(WorkoutSet.logged_at.desc(), WorkoutSet.id.desc()).limit(limit)
    ).all()
    return [_pr_row(s, d) for s, d in rows]


def _pr_row(s: WorkoutSet, d: date) -> dict:
    return {"set_id": s.id, "date": d, "exercise_id": s.exercise_id,
            "exercise": s.exercise.display_name if s.exercise else s.exercise_id, "flags": list(s.pr_flags),
            "reps": s.reps, "weight_kg": s.weight_kg, "e1rm_kg": _set_e1rm(s)}


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def consistency(db: Session, weeks: int = 12, target_per_week: int | None = None, *,
                today: date | None = None) -> dict:
    """Sesiones con al menos un set por semana (lunes a domingo) de las últimas `weeks`, contra la meta
    (`Profile.days_per_week` si no se da). La racha cuenta semanas seguidas cumplidas hasta la anterior;
    la actual suma si ya se cumplió. `days` alimenta el mapa de calor."""
    today = today or local_today()
    if target_per_week is None:
        prof = db.get(Profile, 1)
        target_per_week = prof.days_per_week if prof else 4
    current = _week_start(today)
    first = current - timedelta(weeks=weeks - 1)
    rows = db.execute(
        select(WorkoutSession.date, func.count(func.distinct(WorkoutSession.id)))
        .join(WorkoutSet, WorkoutSet.session_id == WorkoutSession.id)
        .where(WorkoutSession.date >= first, WorkoutSession.date <= today, WorkoutSet.is_warmup.is_(False))
        .group_by(WorkoutSession.date)
    ).all()
    days = {d: n for d, n in rows}
    per_week: dict[date, int] = defaultdict(int)
    for d, n in days.items():
        per_week[_week_start(d)] += n
    out_weeks = []
    for i in range(weeks):
        ws = first + timedelta(weeks=i)
        n = per_week.get(ws, 0)
        out_weeks.append({"week_start": ws, "count": n, "target": target_per_week, "met": n >= target_per_week})
    streak = 0
    hist = out_weeks if out_weeks and out_weeks[-1]["met"] else out_weeks[:-1]
    for w in reversed(hist):
        if not w["met"]:
            break
        streak += 1
    return {"weeks": out_weeks, "streak": streak, "target": target_per_week, "days": days}


def session_summary(db: Session, workout_session_id: int) -> dict:
    """Resumen al cerrar: duración (min), sets de trabajo, ejercicios, tonelaje y PRs."""
    ws = db.get(WorkoutSession, workout_session_id)
    if ws is None:
        raise LookupError(f"No existe la sesión {workout_session_id}")
    work = [s for s in ws.sets if not s.is_warmup]
    end = ws.ended_at or max((s.logged_at for s in ws.sets), default=None) or ws.last_activity_at
    minutes = max(0, round((end - ws.started_at).total_seconds() / 60)) if end and ws.started_at else 0
    names = {s.exercise_id: (s.exercise.display_name if s.exercise else s.exercise_id) for s in work}
    return {
        "session_id": ws.id, "date": ws.date, "duration_min": minutes, "sets": len(work),
        "exercises": len(names), "tonnage_kg": tonnage(work), "session_rpe": ws.session_rpe,
        "prs": [_pr_row(s, ws.date) for s in work if s.pr_flags],
    }



# ── Per-exercise table (Progress → My exercises) ────────────────────────────


def _top(sets: list[WorkoutSet]) -> WorkoutSet | None:
    """The set that best represents a session: heaviest, then most reps (or longest)."""
    return max(sets, key=lambda s: (s.weight_kg or 0, s.reps or 0, s.duration_s or 0), default=None)


def _score(sets: list[WorkoutSet], kind: str) -> float | None:
    if kind == "e1rm":
        vals = [v for s in sets if (v := _set_e1rm(s)) is not None]
    elif kind == "weight":
        vals = [s.weight_kg for s in sets if s.weight_kg]
    elif kind == "time":
        vals = [s.duration_s for s in sets if s.duration_s]
    else:
        vals = [s.reps for s in sets if s.reps]
    return max(vals) if vals else None


def exercise_table(db: Session, since: date | None = None) -> list[dict]:
    """One row per exercise you've done (since `since`): sessions, first and latest top set, best, change between
    the first and the latest session, and a per-session series for a sparkline. The score is the e1RM for loaded
    lifts (the top weight when some session was all high-rep sets), reps for bodyweight work and seconds for
    timed work."""
    from ..core.models import Exercise

    q = (select(WorkoutSet, WorkoutSession.date).join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
         .where(WorkoutSet.is_warmup.is_(False)).order_by(WorkoutSession.date, WorkoutSet.id))
    if since:
        q = q.where(WorkoutSession.date >= since)
    by_ex: dict[str, dict[date, list[WorkoutSet]]] = defaultdict(lambda: defaultdict(list))
    for s, d in db.execute(q).all():
        by_ex[s.exercise_id][d].append(s)
    rows = []
    for ex_id, days in by_ex.items():
        ex = db.get(Exercise, ex_id)
        if ex is None:
            continue
        flat = [s for ss in days.values() for s in ss]
        if any(s.duration_s and not s.reps for s in flat):
            kind = "time"
        elif all(any(_set_e1rm(s) for s in ss) for ss in days.values()):
            kind = "e1rm"  # Epley only holds up to ~12 reps (with RIR): high-rep work falls back to the top weight
        elif any(s.weight_kg for s in flat):
            kind = "weight"
        else:
            kind = "reps"
        series = [(d, v) for d, ss in sorted(days.items()) if (v := _score(ss, kind)) is not None]
        first_d, last_d = min(days), max(days)
        first, last = series[0][1] if series else None, series[-1][1] if series else None
        best = max((v for _d, v in series), default=None)
        rows.append({
            "id": ex_id, "name": ex.display_name, "muscle": ex.primary_muscle, "equipment": ex.equipment_tag,
            "load_kind": ex.load_kind, "kind": kind, "sessions": len(days), "sets": len(flat),
            "first_date": first_d, "first_top": _top(days[first_d]), "last_date": last_d,
            "last_top": _top(days[last_d]), "best": best,
            "best_date": next((d for d, v in series if v == best), None),
            "change_pct": round((last - first) / first * 100, 1) if first and last and len(series) > 1 else None,
            "max_weight": max((s.weight_kg or 0 for s in flat), default=0) or None,
            "tonnage": tonnage(flat), "series": series[-16:],
        })
    return rows
