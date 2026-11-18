"""The Week tab: the seven days at a glance, the program rotation projected forward, and editing a day's plan
before it starts (exercises, sets, reps, loads, order). Plans being trained or already done are read-only."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..body.service import get_profile
from ..core.models import (
    AuditLog,
    EquipmentProfile,
    Exercise,
    PlannedItem,
    PlannedSession,
    ProgramDay,
    WorkoutSession,
)
from ..core.timeutil import local_today
from ..i18n import _
from . import service as tr

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
EDITABLE = ("proposed", "accepted")


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def projected_days(db: Session, start: date, days: int = 7) -> dict[date, ProgramDay]:
    """Which program day each future training day without a plan would get: the rotation continues from
    today, and a plan already set for a day (with its own program day) moves the projection along."""
    prog = tr.active_program(db)
    if not prog or not prog.days:
        return {}
    ids = [d.id for d in prog.days]
    sched = get_profile(db).schedule or {}
    today = local_today()
    idx = prog.rotation_idx
    out: dict[date, ProgramDay] = {}
    d = today
    end = start + timedelta(days=days)
    while d < end:
        plan = tr.plan_for(db, d)
        if plan is not None:  # a rest day or a hand-made plan doesn't use the rotation
            if plan.status not in ("skipped", "completed") and plan.program_day_id in ids:
                idx = ids.index(plan.program_day_id) + 1
        elif WEEKDAYS[d.weekday()] in sched:
            if d >= start:
                out[d] = prog.days[idx % len(ids)]
            idx += 1
        d += timedelta(days=1)
    return out


def week_view(db: Session, start: date) -> dict:
    from ..body import days as daylog

    today = local_today()
    sched = get_profile(db).schedule or {}
    proj = projected_days(db, start)
    marked = daylog.between(db, start, start + timedelta(days=6))
    planned_sets: dict[str, float] = defaultdict(float)
    days = []
    for i in range(7):
        d = start + timedelta(days=i)
        plan = tr.plan_for(db, d)
        ws = db.scalar(select(WorkoutSession).where(WorkoutSession.date == d).order_by(WorkoutSession.id.desc()))
        if plan is not None and plan.status != "skipped":
            for it in plan.items:
                ex = it.exercise
                planned_sets[ex.primary_muscle or "other"] += it.sets
                for m in ex.secondary_muscles or []:
                    if m != ex.primary_muscle:
                        planned_sets[m] += it.sets * 0.5
        days.append({
            "date": d, "key": WEEKDAYS[i], "past": d < today, "today": d == today,
            "training": WEEKDAYS[i] in sched, "plan": plan, "workout": ws,
            "editable": plan is not None and plan.status in EDITABLE and d >= today,
            "projected": proj.get(d), "marked": marked.get(d),
        })
    prog = tr.active_program(db)
    return {"start": start, "days": days, "program_days": list(prog.days) if prog else [],
            "planned_sets": dict(sorted(planned_sets.items(), key=lambda kv: -kv[1]))}


def _editable(plan: PlannedSession) -> None:
    if plan.status not in EDITABLE:
        raise ValueError(_("Ese plan ya empezó o terminó: no se puede editar"))


def _audit(db: Session, tool: str, plan: PlannedSession, after: dict) -> None:
    db.add(AuditLog(actor="user", tool=tool, entity="planned_session", entity_id=str(plan.id), after=after))


def build_day(db: Session, on: date, program_day_id: int | None = None,
              equipment_profile_id: int | None = None) -> PlannedSession:
    """(Re)build a day from the program: the projected program day unless one is chosen."""
    from .planner import build_session

    if on < local_today():
        raise ValueError(_("La fecha no puede ser pasada"))
    current = tr.plan_for(db, on)
    if current is not None and current.status in ("active", "completed"):
        raise ValueError(_("Ese plan ya empezó o terminó: no se puede editar"))
    if program_day_id is None and current is not None and current.program_day_id:
        program_day_id = current.program_day_id
    if program_day_id is None and (pd := projected_days(db, week_start(on), 7 + (on - week_start(on)).days).get(on)):
        program_day_id = pd.id
    if equipment_profile_id is None and current is not None:
        equipment_profile_id = current.equipment_profile_id
    for p in db.scalars(select(PlannedSession).where(PlannedSession.date == on, PlannedSession.status.in_(
            ("proposed", "accepted", "skipped")))):
        p.status = "replaced"
    db.flush()
    plan = build_session(db, on, equipment_profile_id, program_day_id=program_day_id)
    _audit(db, "week_build_day", plan, {"date": on.isoformat(), "program_day_id": program_day_id})
    return plan


def rest_day(db: Session, on: date) -> None:
    plan = tr.plan_for(db, on)
    if plan is not None:
        _editable(plan)
        plan.status = "skipped"
        _audit(db, "week_rest_day", plan, {"date": on.isoformat()})


def update_item(db: Session, item: PlannedItem, *, sets: int, rep_min: int, rep_max: int, rir: int,
                weight_kg: float | None, minutes: float | None = None) -> PlannedItem:
    _editable(item.session)
    if not 1 <= sets <= 10 or not 1 <= rep_min <= rep_max <= 100 or not 0 <= rir <= 5:
        raise ValueError(_("Valores fuera de rango"))
    if weight_kg is not None and not 0 <= weight_kg <= 500:
        raise ValueError(_("Valores fuera de rango"))
    item.sets, item.rep_min, item.rep_max, item.target_rir, item.target_weight_kg = sets, rep_min, rep_max, rir, weight_kg
    if minutes:
        item.slot = {**(item.slot or {}), "duration_min": [minutes, minutes]}
    _audit(db, "week_update_item", item.session, {"item": item.id, "sets": sets, "reps": [rep_min, rep_max],
                                                   "rir": rir, "weight_kg": weight_kg})
    return item


def _target_for(db: Session, plan: PlannedSession, ex: Exercise, slot: dict):
    from .planner import _target

    ep = db.get(EquipmentProfile, plan.equipment_profile_id) if plan.equipment_profile_id else None
    return _target(db, ex, slot, ep, (ep.loads or {}) if ep else {})


def swap_item(db: Session, item: PlannedItem, exercise_id: str) -> PlannedItem:
    """Another exercise in the same slot, with its own target load from your history."""
    _editable(item.session)
    ex = db.get(Exercise, exercise_id)
    if ex is None:
        raise LookupError(exercise_id)
    slot = dict(item.slot or {})
    t = _target_for(db, item.session, ex, {**slot, "rep_min": item.rep_min, "rep_max": item.rep_max,
                                            "target_rir": item.target_rir})
    old = item.exercise_id
    item.exercise_id, item.exercise = ex.id, ex
    item.rep_min, item.rep_max, item.target_weight_kg, item.target_note = t.rep_min, t.rep_max, t.weight_kg, t.note or None
    item.swapped_from_id, item.swap_reason = old, "preference"
    _audit(db, "week_swap_item", item.session, {"item": item.id, "from": old, "to": ex.id})
    return item


def add_item(db: Session, plan: PlannedSession, exercise_id: str, sets: int = 3) -> PlannedItem:
    _editable(plan)
    ex = db.get(Exercise, exercise_id)
    if ex is None:
        raise LookupError(exercise_id)
    timed = ex.load_kind == "duration" and ex.movement_pattern == "cardio"
    lo, hi = (8, 12) if ex.mechanic == "compound" else (10, 15)
    slot = {"slot": "added", "pattern": ex.movement_pattern, "muscle": ex.primary_muscle, "sets": sets,
            "rep_min": lo, "rep_max": hi, "target_rir": 2}
    if timed:
        slot["duration_min"] = [20, 25]
        item = PlannedItem(order_idx=len(plan.items), slot=slot, exercise_id=ex.id, sets=1, rep_min=1, rep_max=1,
                           target_rir=2)
    else:
        t = _target_for(db, plan, ex, slot)
        item = PlannedItem(order_idx=len(plan.items), slot=slot, exercise_id=ex.id, sets=sets, rep_min=t.rep_min,
                           rep_max=t.rep_max, target_rir=2, target_weight_kg=t.weight_kg, target_note=t.note or None)
    plan.items.append(item)
    db.flush()
    _audit(db, "week_add_item", plan, {"item": item.id, "exercise": ex.id})
    return item


def remove_item(db: Session, item: PlannedItem) -> None:
    plan = item.session
    _editable(plan)
    _audit(db, "week_remove_item", plan, {"item": item.id, "exercise": item.exercise_id})
    plan.items.remove(item)
    for i, it in enumerate(plan.items):
        it.order_idx = i


def move_item(db: Session, item: PlannedItem, delta: int) -> None:
    plan = item.session
    _editable(plan)
    items = list(plan.items)
    i = items.index(item)
    j = max(0, min(len(items) - 1, i + delta))
    if i == j:
        return
    items[i], items[j] = items[j], items[i]
    for k, it in enumerate(items):
        it.order_idx = k
    plan.items.sort(key=lambda it: it.order_idx)
