"""Tools del coach (design §8.3, coach v0). Lectura libre; escritura solo validada y auditada.
Esquemas `strict`: `additionalProperties: false`, todo en `required` (los opcionales aceptan null), sin
min/max (los límites se validan aquí)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..body import service as body
from ..core.models import EquipmentProfile, Exercise, PlannedItem, WorkoutSession, WorkoutSet
from ..core.timeutil import local_today
from ..i18n import _
from ..training import service as tr


class ToolError(Exception):
    pass


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required if required is not None else list(props),
            "additionalProperties": False}


NUL_INT = {"type": ["integer", "null"]}
NUL_STR = {"type": ["string", "null"]}
NUL_NUM = {"type": ["number", "null"]}

TOOLS: list[dict] = [
    {"name": "get_profile", "description": "Perfil del usuario: datos, objetivo, experiencia, horario, lesiones, "
     "perfiles de equipo y programa activo con su rotación.", "input_schema": _obj({})},
    {"name": "get_today", "description": "Plan de hoy (ejercicios con sets, rango y peso objetivo, ids de ítems) y la "
     "sesión activa si existe, con los sets hechos.", "input_schema": _obj({})},
    {"name": "get_recent_sessions", "description": "Sesiones recientes compactas: fecha, enfoque, ejercicios con "
     "sets × reps × kg y RPE. `muscle` filtra por músculo principal (chest, lats, quads, ...).",
     "input_schema": _obj({"days": NUL_INT, "muscle": NUL_STR})},
    {"name": "get_exercise_history", "description": "Historial de un ejercicio: últimos sets, tendencia de e1RM y "
     "récords.", "input_schema": _obj({"exercise_id": {"type": "string"}, "limit": NUL_INT})},
    {"name": "search_exercises", "description": "Busca en el catálogo por texto (español o inglés), músculo o "
     "perfil de equipo. Devuelve ids para usar en otras tools.",
     "input_schema": _obj({"query": NUL_STR, "muscle": NUL_STR, "equipment_profile_id": NUL_INT, "limit": NUL_INT})},
    {"name": "find_alternatives", "description": "Los mejores sustitutos (ranking determinista) para un ítem del plan "
     "de hoy. reason: busy | no_equipment | pain | preference.",
     "input_schema": _obj({"planned_item_id": {"type": "integer"}, "reason": {"type": "string",
                           "enum": ["busy", "no_equipment", "pain", "preference"]}})},
    {"name": "get_metrics", "description": "Métricas de entrenamiento: sets duros por músculo esta semana contra la "
     "meta 10–20, tonelaje, constancia de 12 semanas y récords recientes.", "input_schema": _obj({})},
    {"name": "get_labs", "description": "Exámenes de sangre: último valor por marcador con su rango estándar, meta "
     "del usuario y estado. `keys` filtra (p. ej. ldl_c, glucose_fasting); vacío = resumen.",
     "input_schema": _obj({"keys": {"type": ["array", "null"], "items": {"type": "string"}}})},
    {"name": "get_body_metrics", "description": "Serie de peso corporal (kg) y otras medidas.",
     "input_schema": _obj({"kind": NUL_STR})},
    {"name": "swap_exercise", "description": "Cambia el ejercicio de un ítem del plan de hoy por otro del catálogo "
     "(usa find_alternatives antes). El peso objetivo lo recalcula el sistema.",
     "input_schema": _obj({"planned_item_id": {"type": "integer"}, "new_exercise_id": {"type": "string"},
                           "reason": {"type": "string"}})},
    {"name": "log_set", "description": "Registra sets del ejercicio actual de la sesión activa (solo si el usuario los "
     "dio en lenguaje libre y no como comando). weight_kg por mancuerna si son mancuernas.",
     "input_schema": _obj({"sets": {"type": "array", "items": _obj({"reps": {"type": "integer"},
                           "weight_kg": NUL_NUM, "rir": NUL_NUM})}})},
    {"name": "delete_last_set", "description": "Borra el último set registrado de la sesión activa.",
     "input_schema": _obj({})},
    {"name": "log_body_weight", "description": "Registra el peso corporal de hoy en kg.",
     "input_schema": _obj({"weight_kg": {"type": "number"}})},
]
for t in TOOLS:
    t["strict"] = True


def _ex_brief(ex: Exercise) -> dict:
    return {"id": ex.id, "name": ex.display_name, "muscle": ex.primary_muscle, "equipment": ex.equipment_tag,
            "pattern": ex.movement_pattern, "station": ex.station}


def get_profile(db: Session, _a: dict) -> dict:
    p = body.get_profile(db)
    prog = tr.active_program(db)
    w = body.latest_metric(db)
    return {
        "sex": p.sex, "age": body.age(p), "height_cm": p.height_cm, "weight_kg": w.value if w else None,
        "experience": p.experience, "goal": p.primary_goal, "goal_notes": p.goal_notes,
        "days_per_week": p.days_per_week, "session_minutes": p.session_minutes, "schedule": p.schedule,
        "injuries": p.injuries, "coach_notes": p.coach_notes,
        "equipment_profiles": [{"id": e.id, "name": e.name, "tags": e.tags} for e in body.equipment_profiles(db)],
        "program": {"name": prog.name, "days": [d.name for d in prog.days], "next_day_idx": prog.rotation_idx}
        if prog else None,
    }


def get_today(db: Session, _a: dict) -> dict:
    plan = tr.plan_for(db, local_today())
    ws = tr.active_session(db)
    out: dict[str, Any] = {"date": local_today().isoformat(), "plan": None, "active_session": None}
    if plan:
        out["plan"] = {"focus": plan.focus_label, "status": plan.status, "rationale": plan.rationale,
                       "items": [{"planned_item_id": i.id, **_ex_brief(i.exercise), "sets": i.sets,
                                  "reps": f"{i.rep_min}-{i.rep_max}", "target_kg": i.target_weight_kg,
                                  "target_rir": i.target_rir, "status": i.status} for i in plan.items]}
    if ws:
        cur = tr.current_item(db, ws)
        out["active_session"] = {"state": ws.state, "current_planned_item_id": cur.id if cur else None,
                                 "sets": [{"exercise": s.exercise.display_name, "reps": s.reps, "kg": s.weight_kg,
                                           "rir": s.rir} for s in ws.sets]}
    return out


def get_recent_sessions(db: Session, a: dict) -> list[dict]:
    days = min(max(int(a.get("days") or 14), 1), 90)
    since = local_today() - timedelta(days=days)
    out = []
    for ws in db.scalars(select(WorkoutSession).where(WorkoutSession.date >= since).order_by(WorkoutSession.date)):
        by_ex: dict[str, list[str]] = {}
        for s in ws.sets:
            if a.get("muscle") and s.exercise.primary_muscle != a["muscle"]:
                continue
            by_ex.setdefault(s.exercise.display_name, []).append(f"{s.reps}x{s.weight_kg or 'bw'}")
        out.append({"date": ws.date.isoformat(), "focus": ws.planned.focus_label if ws.planned else None,
                    "rpe": ws.session_rpe, "exercises": by_ex})
    return out


def get_exercise_history(db: Session, a: dict) -> dict:
    ex = db.get(Exercise, a["exercise_id"])
    if not ex:
        raise ToolError(_("ejercicio no encontrado"))
    limit = min(max(int(a.get("limit") or 30), 1), 100)
    rows = db.scalars(select(WorkoutSet).join(WorkoutSession).where(WorkoutSet.exercise_id == ex.id)
                      .order_by(WorkoutSession.date.desc(), WorkoutSet.id).limit(limit)).all()
    from ..training.metrics import e1rm_series

    return {"exercise": _ex_brief(ex),
            "sets": [{"date": s.session.date.isoformat(), "reps": s.reps, "kg": s.weight_kg, "rir": s.rir,
                      "e1rm": round(s.e1rm_kg, 1) if s.e1rm_kg else None, "prs": s.pr_flags} for s in rows],
            "e1rm_trend": [(d.isoformat(), round(v, 1)) for d, v in e1rm_series(db, ex.id)][-12:]}


def search_exercises(db: Session, a: dict) -> list[dict]:
    from ..catalog.service import search

    tags = None
    if a.get("equipment_profile_id"):
        ep = db.get(EquipmentProfile, a["equipment_profile_id"])
        tags = set(ep.tags) if ep else None
    limit = min(max(int(a.get("limit") or 10), 1), 25)
    rows = search(db, q=a.get("query") or "", muscle=a.get("muscle"), profile_tags=tags, limit=limit)
    return [_ex_brief(e) for e in rows]


def _today_item(db: Session, item_id: int) -> PlannedItem:
    item = db.get(PlannedItem, item_id)
    if not item or item.session.date != local_today():
        raise ToolError(_("ese ítem no es del plan de hoy"))
    return item


def find_alternatives(db: Session, a: dict) -> list[dict]:
    item = _today_item(db, int(a["planned_item_id"]))
    return [{**_ex_brief(e), "score": round(s, 1)} for e, s in tr.alternatives(db, tr.active_session(db), item)]


def get_metrics(db: Session, _a: dict) -> dict:
    from ..training import metrics as m

    today = local_today()
    week_start = today - timedelta(days=today.weekday())
    prof = body.get_profile(db)
    return {"week_start": week_start.isoformat(),
            "hard_sets_by_muscle": {k: round(v, 1) for k, v in m.weekly_hard_sets_by_muscle(db, week_start).items()},
            "consistency": m.consistency(db, weeks=12, target_per_week=prof.days_per_week),
            "recent_prs": m.recent_prs(db, 10)}


def get_labs(db: Session, a: dict) -> Any:
    from ..labs import service as labs

    prof = body.get_profile(db)
    data = labs.overview(db, prof.sex, prof.birth_date)
    keys = set(a.get("keys") or [])
    return json.loads(json.dumps(_filter_labs(data, keys), default=str))


def _filter_labs(data: Any, keys: set[str]) -> Any:
    if not keys:
        return data
    if isinstance(data, dict):
        if data.get("key") in keys:
            return data
        out = {k: _filter_labs(v, keys) for k, v in data.items()}
        return {k: v for k, v in out.items() if v not in (None, [], {})}
    if isinstance(data, list):
        return [x for x in (_filter_labs(v, keys) for v in data) if x not in (None, [], {})]
    return None


def get_body_metrics(db: Session, a: dict) -> list[dict]:
    kind = a.get("kind") or "weight_kg"
    return [{"date": m.measured_on.isoformat(), "value": m.value} for m in body.metric_series(db, kind, 120)]


def swap_exercise(db: Session, a: dict) -> dict:
    item = _today_item(db, int(a["planned_item_id"]))
    ex = db.get(Exercise, a["new_exercise_id"])
    if not ex or not ex.enabled:
        raise ToolError(_("ejercicio no encontrado o deshabilitado"))
    ep = db.get(EquipmentProfile, item.session.equipment_profile_id) if item.session.equipment_profile_id else None
    from ..training.substitution import eligible

    if ep and not eligible(ex, set(ep.tags)):
        raise ToolError(_("{name} no es posible con el equipo de {place}", name=ex.display_name, place=body.equipment_name(ep)))
    tr.swap_item(db, tr.active_session(db), item, ex, (a.get("reason") or "coach")[:32], actor="llm")
    return {"ok": True, "item": item.id, "exercise": _ex_brief(ex), "target_kg": item.target_weight_kg}


def log_set(db: Session, a: dict) -> dict:
    from ..wa.parser import SetEntry

    ws = tr.active_session(db)
    if not ws:
        raise ToolError(_("no hay sesión activa"))
    item = tr.current_item(db, ws)
    if not item:
        raise ToolError(_("no hay ejercicio actual"))
    entries = []
    for s in a.get("sets") or []:
        reps, kg, rir = s.get("reps"), s.get("weight_kg"), s.get("rir")
        if not (isinstance(reps, int) and 1 <= reps <= 100) or (kg is not None and not 0 <= kg <= 500) or (
                rir is not None and not 0 <= rir <= 10):
            raise ToolError(_("set fuera de rango: {set}", set=s))
        entries.append(SetEntry(reps=reps, weight_kg=kg, rir=rir))
    if not entries or len(entries) > 10:
        raise ToolError(_("entre 1 y 10 sets"))
    out = tr.log_sets(db, ws, item, entries, raw_text="(coach)", actor="llm")
    return {"ok": True, "logged": [{"set": s.set_idx, "reps": s.reps, "kg": s.weight_kg} for s in out],
            "exercise": item.exercise.display_name}


def delete_last_set(db: Session, _a: dict) -> dict:
    ws = tr.active_session(db)
    if not ws:
        raise ToolError(_("no hay sesión activa"))
    last = tr.undo_last_set(db, ws, actor="llm")
    return {"ok": bool(last)}


def log_body_weight(db: Session, a: dict) -> dict:
    try:
        m = body.log_metric(db, "weight_kg", float(a["weight_kg"]), source="coach")
    except ValueError as e:
        raise ToolError(str(e)) from e
    return {"ok": True, "date": m.measured_on.isoformat(), "kg": m.value}


HANDLERS: dict[str, Callable[[Session, dict], Any]] = {
    "get_profile": get_profile, "get_today": get_today, "get_recent_sessions": get_recent_sessions,
    "get_exercise_history": get_exercise_history, "search_exercises": search_exercises,
    "find_alternatives": find_alternatives, "get_metrics": get_metrics, "get_labs": get_labs,
    "get_body_metrics": get_body_metrics, "swap_exercise": swap_exercise, "log_set": log_set,
    "delete_last_set": delete_last_set, "log_body_weight": log_body_weight,
}
