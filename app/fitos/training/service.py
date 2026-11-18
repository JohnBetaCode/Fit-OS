"""Operaciones de entrenamiento sobre la base: plan del día, sesión activa, sets, cambios y cierre.
Sin WhatsApp ni web: `wa/session_flow.py` y las rutas llaman aquí."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..body.service import ensure_defaults, get_profile, injured_joints
from ..core.models import (
    AuditLog,
    EquipmentProfile,
    Exercise,
    PlannedItem,
    PlannedSession,
    Program,
    WorkoutSession,
    WorkoutSet,
)
from ..core.timeutil import local_today, utcnow
from ..i18n import _
from ..wa.parser import SetEntry
from . import progression

ACTIVE_STATES = ("active", "finishing")


# ── Programa y plan ─────────────────────────────────────────────────────────


def active_program(db: Session) -> Program | None:
    return db.scalar(select(Program).where(Program.status == "active").order_by(Program.id.desc()).limit(1))


def ensure_program(db: Session) -> Program:
    """Si no hay programa activo, crea uno desde la plantilla que toca según los días por semana."""
    prog = active_program(db)
    if prog:
        return prog
    from .planner import create_program_from_template, load_templates

    prof = ensure_defaults(db)
    tpl = load_templates()
    key = (tpl.get("default_for_days") or {}).get(prof.days_per_week) or (tpl.get("default_for_days") or {}).get(
        str(prof.days_per_week)
    ) or "upper_lower_4"
    return create_program_from_template(db, key, prof.days_per_week)


def plan_for(db: Session, on: date) -> PlannedSession | None:
    return db.scalar(
        select(PlannedSession)
        .where(PlannedSession.date == on, PlannedSession.status.notin_(("replaced",)))
        .order_by(PlannedSession.id.desc())
        .limit(1)
    )


def get_or_build_plan(db: Session, on: date | None = None, equipment_profile_id: int | None = None,
                      replace: bool = False) -> PlannedSession:
    from .planner import build_session

    on = on or local_today()
    ensure_program(db)
    existing = plan_for(db, on)
    if existing and not replace and existing.status != "skipped":
        return existing
    return build_session(db, on, equipment_profile_id, replace=True if existing else replace)


def is_training_day(db: Session, on: date) -> bool:
    prof = get_profile(db)
    key = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][on.weekday()]
    return key in (prof.schedule or {})


def skip_today(db: Session, on: date | None = None) -> PlannedSession | None:
    plan = plan_for(db, on or local_today())
    if plan and plan.status in ("proposed", "accepted"):
        plan.status = "skipped"
    return plan


# ── Sesión activa ───────────────────────────────────────────────────────────


def active_session(db: Session) -> WorkoutSession | None:
    return db.scalar(
        select(WorkoutSession).where(WorkoutSession.state.in_(ACTIVE_STATES)).order_by(WorkoutSession.id.desc()).limit(1)
    )


def start_session(db: Session, plan: PlannedSession, source: str = "wa") -> WorkoutSession:
    current = active_session(db)
    if current:
        return current
    plan.status = "active"
    ws = WorkoutSession(
        planned_session_id=plan.id, date=plan.date, equipment_profile_id=plan.equipment_profile_id,
        state="active", source=source, wa_context={"busy_stations": [], "excluded_tags": [], "alt_cursor": {}},
    )
    db.add(ws)
    db.flush()
    first = next_pending_item(plan)
    if first:
        first.status = "active"
        ws.wa_context = {**ws.wa_context, "current_item_id": first.id}
    return ws


def next_pending_item(plan: PlannedSession) -> PlannedItem | None:
    return next((i for i in plan.items if i.status in ("pending", "active")), None)


def current_item(db: Session, ws: WorkoutSession) -> PlannedItem | None:
    iid = (ws.wa_context or {}).get("current_item_id")
    return db.get(PlannedItem, iid) if iid else None


def item_sets(ws: WorkoutSession, item: PlannedItem) -> list[WorkoutSet]:
    return [s for s in ws.sets if s.planned_item_id == item.id and not s.is_warmup]


def advance(db: Session, ws: WorkoutSession, status: str = "done") -> PlannedItem | None:
    """Cierra el ítem actual con `status` y activa el siguiente. Devuelve el siguiente o None."""
    item = current_item(db, ws)
    if item and item.status in ("pending", "active"):
        item.status = status
    plan = ws.planned
    nxt = next_pending_item(plan) if plan else None
    ctx = dict(ws.wa_context or {})
    if nxt:
        nxt.status = "active"
        ctx["current_item_id"] = nxt.id
    else:
        ctx["current_item_id"] = None
    ws.wa_context = ctx
    return nxt


def _prior_best(db: Session, exercise_id: str, exclude_session_id: int) -> dict:
    rows = db.scalars(
        select(WorkoutSet).where(WorkoutSet.exercise_id == exercise_id, WorkoutSet.session_id != exclude_session_id,
                                 WorkoutSet.is_warmup.is_(False))
    ).all()
    best = {"e1rm": 0.0, "weight": 0.0, "reps_at_weight": {}}
    for s in rows:
        if s.e1rm_kg:
            best["e1rm"] = max(best["e1rm"], s.e1rm_kg)
        if s.weight_kg:
            best["weight"] = max(best["weight"], s.weight_kg)
            if s.reps:
                best["reps_at_weight"][s.weight_kg] = max(best["reps_at_weight"].get(s.weight_kg, 0), s.reps)
    best["has_history"] = bool(rows)
    return best


def log_sets(db: Session, ws: WorkoutSession, item: PlannedItem, entries: list[SetEntry], raw_text: str = "",
             actor: str = "user", warmup: bool = False) -> list[WorkoutSet]:
    """Guarda sets del ítem actual, con e1RM y récords (solo si ya había historial del ejercicio).
    Warm-up sets (`warmup=True`) get `set_idx` 0 and count for nothing: no PRs, no volume, no progression."""
    ex = item.exercise
    if warmup:
        out = []
        for e in entries:
            st = WorkoutSet(session_id=ws.id, planned_item_id=item.id, exercise_id=ex.id, set_idx=0, reps=e.reps,
                            weight_kg=e.added_kg if e.bodyweight and e.added_kg else e.weight_kg, rir=e.rir,
                            duration_s=e.duration_s, is_warmup=True, raw_text=raw_text[:200] or None)
            db.add(st)
            out.append(st)
        db.flush()
        db.refresh(ws)
        ws.last_activity_at = utcnow()
        return out
    prior = _prior_best(db, ex.id, ws.id)
    done_here = item_sets(ws, item)
    out = []
    for e in entries:
        weight = e.weight_kg
        if e.bodyweight and e.added_kg:
            weight = e.added_kg
        idx = len(done_here) + len(out) + 1
        st = WorkoutSet(
            session_id=ws.id, planned_item_id=item.id, exercise_id=ex.id, set_idx=idx,
            target_rep_min=item.rep_min, target_rep_max=item.rep_max, target_weight_kg=item.target_weight_kg,
            target_rir=item.target_rir, reps=e.reps, weight_kg=weight, rir=e.rir, duration_s=e.duration_s,
            raw_text=raw_text[:200] or None,
        )
        if ex.load_kind not in ("bodyweight", "duration") and weight and e.reps:
            st.e1rm_kg = progression.e1rm(weight, e.reps, e.rir)
        if prior["has_history"] and weight and e.reps:
            flags = progression.detect_prs([progression.SetPerf(e.reps, weight, e.rir)], prior)
            st.pr_flags = flags
            if flags:  # el récord nuevo sube la vara para los sets siguientes de esta misma sesión
                if st.e1rm_kg:
                    prior["e1rm"] = max(prior["e1rm"], st.e1rm_kg)
                prior["weight"] = max(prior["weight"], weight)
                prior["reps_at_weight"][weight] = max(prior["reps_at_weight"].get(weight, 0), e.reps)
        db.add(st)
        out.append(st)
    db.flush()
    db.refresh(ws)
    ws.last_activity_at = utcnow()
    db.add(AuditLog(actor=actor, tool="log_sets", entity="workout_session", entity_id=str(ws.id),
                    after=[{"reps": s.reps, "weight_kg": s.weight_kg, "rir": s.rir} for s in out]))
    return out


def undo_last_set(db: Session, ws: WorkoutSession, actor: str = "user") -> WorkoutSet | None:
    last = db.scalar(select(WorkoutSet).where(WorkoutSet.session_id == ws.id).order_by(WorkoutSet.id.desc()).limit(1))
    if last:
        db.add(AuditLog(actor=actor, tool="delete_set", entity="workout_set", entity_id=str(last.id),
                        before={"reps": last.reps, "weight_kg": last.weight_kg, "rir": last.rir}))
        db.delete(last)
        db.flush()
        db.refresh(ws)
    return last


def edit_last_set(db: Session, ws: WorkoutSession, entry: SetEntry, actor: str = "user") -> WorkoutSet | None:
    last = db.scalar(select(WorkoutSet).where(WorkoutSet.session_id == ws.id).order_by(WorkoutSet.id.desc()).limit(1))
    if not last:
        return None
    before = {"reps": last.reps, "weight_kg": last.weight_kg, "rir": last.rir}
    last.reps, last.rir = entry.reps, entry.rir
    if entry.weight_kg is not None:
        last.weight_kg = entry.weight_kg
    ex = db.get(Exercise, last.exercise_id)
    last.e1rm_kg = progression.e1rm(last.weight_kg, last.reps, last.rir) if ex and ex.load_kind not in (
        "bodyweight", "duration") and last.weight_kg and last.reps else None
    db.add(AuditLog(actor=actor, tool="edit_set", entity="workout_set", entity_id=str(last.id), before=before,
                    after={"reps": last.reps, "weight_kg": last.weight_kg, "rir": last.rir}))
    return last


def swap_item(db: Session, ws: WorkoutSession | None, item: PlannedItem, new_ex: Exercise, reason: str,
              actor: str = "user") -> PlannedItem:
    """Cambia el ejercicio del ítem (mismo slot, sets, rango y RIR); el peso sale del historial del nuevo."""
    from .planner import history_for

    old = item.exercise_id
    if not item.swapped_from_id:
        item.swapped_from_id = old
    item.exercise_id = new_ex.id
    item.swap_reason = reason
    db.flush()
    db.refresh(item)
    ep = db.get(EquipmentProfile, (ws.equipment_profile_id if ws else None) or item.session.equipment_profile_id)
    loads = ep.loads if ep else {}
    hist = history_for(db, new_ex.id, ep.id if ep else None)
    tgt = progression.next_target(
        hist, item.rep_min, item.rep_max, item.target_rir,
        {"equipment_tag": new_ex.equipment_tag, "load_kind": new_ex.load_kind,
         "primary_muscle": new_ex.primary_muscle, "mechanic": new_ex.mechanic},
        loads,
    )
    item.target_weight_kg = tgt.weight_kg
    item.target_note = getattr(tgt, "note", None)
    db.add(AuditLog(actor=actor, tool="swap_exercise", entity="planned_item", entity_id=str(item.id),
                    before={"exercise_id": old}, after={"exercise_id": new_ex.id, "reason": reason}))
    return item


def alternatives(db: Session, ws: WorkoutSession | None, item: PlannedItem, limit: int = 5):
    from .substitution import rank_alternatives

    prof = get_profile(db)
    plan = item.session
    ep = db.get(EquipmentProfile, (ws.equipment_profile_id if ws else None) or plan.equipment_profile_id)
    tags = set(ep.tags if ep else [])
    ctx = (ws.wa_context or {}) if ws else {}
    in_session = {i.exercise_id for i in plan.items}
    recent_since = local_today() - timedelta(days=2)
    recent = set(db.scalars(
        select(WorkoutSet.exercise_id).join(WorkoutSession).where(WorkoutSession.date >= recent_since)
    ).all())
    history = set(db.scalars(select(WorkoutSet.exercise_id).distinct()).all())
    original = db.get(Exercise, item.swapped_from_id or item.exercise_id) or item.exercise
    return rank_alternatives(
        db, original, tags,
        busy_stations=set(ctx.get("busy_stations") or []),
        excluded_tags=set(ctx.get("excluded_tags") or []),
        exclude_ids=in_session | set(prof.disliked_exercise_ids or []),
        injured_joints=injured_joints(prof), recent_ids=recent, history_ids=history, limit=limit,
    )


def finish_session(db: Session, ws: WorkoutSession, rpe: int | None = None) -> WorkoutSession:
    ws.state = "completed"
    ws.ended_at = ws.ended_at or utcnow()
    if rpe is not None:
        ws.session_rpe = rpe
    plan = ws.planned
    if plan:
        for it in plan.items:
            if it.status in ("pending", "active"):
                it.status = "done" if item_sets(ws, it) else "skipped"
        plan.status = "completed"
        prog = active_program(db)
        if prog and plan.program_day_id and prog.days:
            day_ids = [d.id for d in prog.days]
            if plan.program_day_id in day_ids:
                prog.rotation_idx = (day_ids.index(plan.program_day_id) + 1) % len(day_ids)
    return ws


def close_day(db: Session) -> str | None:
    """Fin del día: planes no iniciados → skipped (la rotación no avanza)."""
    today = local_today()
    n = 0
    for p in db.scalars(select(PlannedSession).where(PlannedSession.date <= today,
                                                     PlannedSession.status.in_(("proposed", "accepted")))):
        p.status = "skipped"
        n += 1
    return _("{n} planes sin iniciar → skipped", n=n)


def recent_sessions(db: Session, limit: int = 30) -> list[WorkoutSession]:
    return list(db.scalars(select(WorkoutSession).order_by(WorkoutSession.date.desc(), WorkoutSession.id.desc())
                           .limit(limit)))


# ── Editing past workouts (Workouts tab) ────────────────────────────────────


def _e1rm_for(ex: Exercise | None, weight: float | None, reps: int | None, rir: float | None) -> float | None:
    if ex is not None and ex.load_kind in ("bodyweight", "duration"):
        return None
    return progression.e1rm(weight, reps, rir) if weight and reps else None


def _check_set(reps: int, weight_kg: float | None, rir: float | None) -> None:
    if not 1 <= reps <= 100:
        raise ValueError(_("Valores fuera de rango"))
    if weight_kg is not None and not 0 <= weight_kg <= 500:
        raise ValueError(_("Valores fuera de rango"))
    if rir is not None and not 0 <= rir <= 10:
        raise ValueError(_("Valores fuera de rango"))


def recompute_prs(db: Session, exercise_ids) -> None:
    """Re-derive `e1rm_kg` and `pr_flags` of every set of these exercises, oldest session first, with the
    same rules as `log_sets`: no PR without earlier history, and a PR raises the bar for the rest of its
    session. Called after a past set or workout is edited or deleted."""
    db.flush()
    for ex_id in set(exercise_ids):
        ex = db.get(Exercise, ex_id)
        rows = db.execute(
            select(WorkoutSet, WorkoutSession.id)
            .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
            .where(WorkoutSet.exercise_id == ex_id)
            .order_by(WorkoutSession.date, WorkoutSession.id, WorkoutSet.id)
        ).all()
        best = {"e1rm": 0.0, "weight": 0.0, "reps_at_weight": {}}
        has_history = False
        by_session: dict[int, list[WorkoutSet]] = {}
        for st, sid in rows:
            by_session.setdefault(sid, []).append(st)
        for sets in by_session.values():
            prior = {"e1rm": best["e1rm"], "weight": best["weight"], "reps_at_weight": dict(best["reps_at_weight"])}
            work = [s for s in sets if not s.is_warmup]
            for st in sets:
                st.e1rm_kg = _e1rm_for(ex, st.weight_kg, st.reps, st.rir)
                flags: list[str] = []
                if not st.is_warmup and has_history and st.weight_kg and st.reps:
                    flags = progression.detect_prs([progression.SetPerf(st.reps, st.weight_kg, st.rir)], prior)
                    if flags:
                        if st.e1rm_kg:
                            prior["e1rm"] = max(prior["e1rm"], st.e1rm_kg)
                        prior["weight"] = max(prior["weight"], st.weight_kg)
                        prior["reps_at_weight"][st.weight_kg] = max(prior["reps_at_weight"].get(st.weight_kg, 0),
                                                                    st.reps)
                if list(st.pr_flags or []) != flags:
                    st.pr_flags = flags
            for s in work:
                if s.e1rm_kg:
                    best["e1rm"] = max(best["e1rm"], s.e1rm_kg)
                if s.weight_kg:
                    best["weight"] = max(best["weight"], s.weight_kg)
                    if s.reps:
                        best["reps_at_weight"][s.weight_kg] = max(best["reps_at_weight"].get(s.weight_kg, 0), s.reps)
            has_history = has_history or bool(work)


def _renumber(db: Session, session_id: int, exercise_id: str) -> None:
    db.flush()
    sets = db.scalars(select(WorkoutSet).where(WorkoutSet.session_id == session_id, WorkoutSet.exercise_id == exercise_id,
                                               WorkoutSet.is_warmup.is_(False)).order_by(WorkoutSet.id)).all()
    for i, s in enumerate(sets, start=1):
        s.set_idx = i


def _set_snapshot(s: WorkoutSet) -> dict:
    return {"exercise_id": s.exercise_id, "set_idx": s.set_idx, "reps": s.reps, "weight_kg": s.weight_kg,
            "rir": s.rir, "duration_s": s.duration_s, "is_warmup": s.is_warmup}


def update_set(db: Session, st: WorkoutSet, reps: int, weight_kg: float | None, rir: float | None,
               actor: str = "user") -> WorkoutSet:
    _check_set(reps, weight_kg, rir)
    before = _set_snapshot(st)
    st.reps, st.weight_kg, st.rir = reps, weight_kg, rir
    db.add(AuditLog(actor=actor, tool="web_edit_set", entity="workout_set", entity_id=str(st.id), before=before,
                    after=_set_snapshot(st)))
    recompute_prs(db, [st.exercise_id])
    return st


def delete_set(db: Session, st: WorkoutSet, actor: str = "user") -> None:
    sid, ex_id = st.session_id, st.exercise_id
    db.add(AuditLog(actor=actor, tool="web_delete_set", entity="workout_set", entity_id=str(st.id),
                    before=_set_snapshot(st)))
    db.delete(st)
    _renumber(db, sid, ex_id)
    recompute_prs(db, [ex_id])


def add_set(db: Session, ws: WorkoutSession, exercise_id: str, reps: int, weight_kg: float | None,
            rir: float | None, actor: str = "user") -> WorkoutSet:
    """Append a set the user forgot to log (or logged wrong) to a workout, after the last one of that exercise."""
    _check_set(reps, weight_kg, rir)
    ex = db.get(Exercise, exercise_id)
    if ex is None:
        raise LookupError(exercise_id)
    item = next((it for it in (ws.planned.items if ws.planned else []) if it.exercise_id == exercise_id), None)
    n = sum(1 for s in ws.sets if s.exercise_id == exercise_id and not s.is_warmup)
    st = WorkoutSet(session_id=ws.id, planned_item_id=item.id if item else None, exercise_id=exercise_id,
                    set_idx=n + 1, reps=reps, weight_kg=weight_kg, rir=rir, raw_text=None,
                    target_rep_min=item.rep_min if item else None, target_rep_max=item.rep_max if item else None,
                    target_weight_kg=item.target_weight_kg if item else None,
                    target_rir=item.target_rir if item else None)
    db.add(st)
    db.flush()
    db.add(AuditLog(actor=actor, tool="web_add_set", entity="workout_set", entity_id=str(st.id),
                    after=_set_snapshot(st)))
    recompute_prs(db, [exercise_id])
    db.refresh(ws)
    return st


def update_session(db: Session, ws: WorkoutSession, *, on: date, rpe: int | None, duration_min: int | None,
                   notes: str | None, actor: str = "user") -> WorkoutSession:
    """Edit a workout's date, session RPE, duration and notes."""
    if on > local_today():
        raise ValueError(_("La fecha no puede ser futura"))
    if rpe is not None and not 1 <= rpe <= 10:
        raise ValueError(_("Valores fuera de rango"))
    if duration_min is not None and not 1 <= duration_min <= 600:
        raise ValueError(_("Valores fuera de rango"))
    before = {"date": ws.date.isoformat(), "session_rpe": ws.session_rpe, "notes": ws.notes,
              "ended_at": ws.ended_at.isoformat() if ws.ended_at else None}
    ws.date, ws.session_rpe = on, rpe
    ws.notes = (notes or "").strip()[:1000] or None
    if duration_min is not None and ws.started_at:
        ws.ended_at = ws.started_at + timedelta(minutes=duration_min)
    db.add(AuditLog(actor=actor, tool="web_edit_session", entity="workout_session", entity_id=str(ws.id),
                    before=before, after={"date": on.isoformat(), "session_rpe": rpe, "notes": ws.notes,
                                          "ended_at": ws.ended_at.isoformat() if ws.ended_at else None}))
    if before["date"] != on.isoformat():  # PRs depend on the order of sessions
        recompute_prs(db, {s.exercise_id for s in ws.sets})
    return ws


def delete_session(db: Session, ws: WorkoutSession, actor: str = "user") -> None:
    """Delete a workout and its sets. Its plan goes back to "proposed" if it is for today (so it can be started
    again) or to "skipped" if it is in the past; the program rotation steps back if this was the workout that
    advanced it. The audit log keeps a full copy of what was deleted."""
    ex_ids = {s.exercise_id for s in ws.sets}
    plan = ws.planned
    db.add(AuditLog(actor=actor, tool="web_delete_session", entity="workout_session", entity_id=str(ws.id),
                    before={"date": ws.date.isoformat(), "state": ws.state, "session_rpe": ws.session_rpe,
                            "notes": ws.notes, "planned_session_id": ws.planned_session_id,
                            "focus": plan.focus_label if plan else None,
                            "sets": [_set_snapshot(s) for s in ws.sets]}))
    if plan is not None:
        _step_rotation_back(db, plan)
        if plan.date >= local_today():
            plan.status = "proposed"
            for it in plan.items:
                if it.status in ("active", "done", "skipped"):
                    it.status = "pending"
        else:
            plan.status = "skipped"
    db.delete(ws)
    db.flush()
    recompute_prs(db, ex_ids)


def _step_rotation_back(db: Session, plan: PlannedSession) -> None:
    prog = active_program(db)
    if plan.status != "completed" or not prog or not prog.days or not plan.program_day_id:
        return
    day_ids = [d.id for d in prog.days]
    if plan.program_day_id not in day_ids:
        return
    latest = db.scalar(select(PlannedSession).where(PlannedSession.status == "completed")
                       .order_by(PlannedSession.date.desc(), PlannedSession.id.desc()).limit(1))
    idx = day_ids.index(plan.program_day_id)
    if latest is not None and latest.id == plan.id and prog.rotation_idx == (idx + 1) % len(day_ids):
        prog.rotation_idx = idx


# ── Hand-made plans (CLI today, the Week tab later) ─────────────────────────


def put_custom_plan(db: Session, on: date, focus: str, items: list[dict], *, equipment_profile_id: int | None = None,
                    rationale: str | None = None) -> PlannedSession:
    """Plan for `on` with exactly these exercises, replacing a not-started plan of that day. Each item:
    `{exercise: id, sets: 3, reps: [8, 10] | 10, rir: 2, weight_kg?: 60, minutes?: [20, 25] | 20, note?: "..."}`.
    `minutes` makes it a timed item (cardio). Without `weight_kg` the load comes from the usual progression on
    your history (or a calibration note the first time). It doesn't advance the program rotation."""
    from .planner import _equipment_profile, _target

    if not items:
        raise ValueError(_("El plan está vacío"))
    current = plan_for(db, on)
    if current is not None and current.status in ("active", "completed"):
        raise ValueError(_("El plan de ese día ya está {status}", status=current.status))
    prof = get_profile(db)
    ep = _equipment_profile(db, on, equipment_profile_id, prof)
    loads = (ep.loads or {}) if ep else {}
    plan = PlannedSession(date=on, program_day_id=None, focus_label=focus[:80], equipment_profile_id=ep.id if ep else None,
                          status="proposed", source="custom", rationale=rationale)
    for spec in items:
        ex = db.get(Exercise, str(spec["exercise"]))
        if ex is None:
            raise LookupError(_("No existe el ejercicio {id}", id=spec["exercise"]))
        sets = int(spec.get("sets", 3))
        rir = int(spec.get("rir", 2))
        reps = spec.get("reps", [8, 12])
        lo, hi = (int(reps), int(reps)) if isinstance(reps, int | float) else (int(reps[0]), int(reps[-1]))
        slot = {"slot": "custom", "pattern": ex.movement_pattern, "muscle": ex.primary_muscle, "sets": sets,
                "rep_min": lo, "rep_max": hi, "target_rir": rir, "preferred_exercise_ids": [ex.id]}
        weight, note = spec.get("weight_kg"), spec.get("note")
        if spec.get("minutes"):
            mins = spec["minutes"]
            slot["duration_min"] = [mins, mins] if isinstance(mins, int | float) else [mins[0], mins[-1]]
            lo = hi = 1
        else:
            t = _target(db, ex, slot, ep, loads)
            lo, hi = t.rep_min, t.rep_max
            weight = t.weight_kg if weight is None else float(weight)
            note = note or t.note
        if not 1 <= sets <= 10 or not 1 <= lo <= hi <= 100 or not 0 <= rir <= 5:
            raise ValueError(_("Valores fuera de rango"))
        plan.items.append(PlannedItem(order_idx=len(plan.items), slot=slot, exercise_id=ex.id, sets=sets,
                                      rep_min=lo, rep_max=hi, target_rir=rir, target_weight_kg=weight,
                                      target_note=note or None))
    for old in db.scalars(select(PlannedSession).where(PlannedSession.date == on,
                                                      PlannedSession.status.in_(("proposed", "accepted", "skipped")))):
        old.status = "replaced"
    db.add(plan)
    db.flush()
    db.add(AuditLog(actor="user", tool="put_custom_plan", entity="planned_session", entity_id=str(plan.id),
                    after={"date": on.isoformat(), "focus": focus, "items": items}))
    return plan
