"""Planificador determinista (design §7.1): plantillas → programa, y el plan del día por slots."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import yaml
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core.models import (
    EquipmentProfile,
    Exercise,
    PlannedItem,
    PlannedSession,
    Profile,
    Program,
    ProgramDay,
    WorkoutSession,
    WorkoutSet,
)
from ..core.timeutil import local_today
from ..i18n import _
from .progression import PlannedTarget, SessionPerf, SetPerf, next_target
from .substitution import active_injured_joints, eligible

WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
EQUIPMENT_TAGS = [
    "bodyweight", "dumbbell", "cable", "barbell", "machine", "band", "smith", "kettlebell", "plate",
    "stability_ball", "medicine_ball", "bosu", "ez_bar", "trap_bar", "assisted_machine", "sled", "rope",
    "foam_roller", "ab_wheel", "cardio_machine", "other",
]
REQUIRE_TAGS = ["bench_flat", "bench_incline", "bench_decline", "pullup_bar", "dip_bars", "rack", "preacher_bench", "box"]
ALL_TAGS = frozenset(EQUIPMENT_TAGS + REQUIRE_TAGS)
RECENT_DAYS = 1  # "últimas 48 h" ≈ hoy y ayer (fechas de calendario)


class NoProgramError(RuntimeError):
    """No hay programa activo (o no tiene días): hay que crear uno desde una plantilla."""


# ── Plantillas y programa ───────────────────────────────────────────────────


def _templates_path() -> Path:
    """`FITOS_CATALOG_DIR` > `/catalog` (compose de dev) > `catalog/` en la raíz del repo."""
    candidates = []
    if env := os.environ.get("FITOS_CATALOG_DIR"):
        candidates.append(Path(env))
    candidates += [Path("/catalog"), Path(__file__).resolve().parents[3] / "catalog"]
    for c in candidates:
        if (c / "program_templates.yaml").is_file():
            return c / "program_templates.yaml"
    raise FileNotFoundError(_("No encuentro catalog/program_templates.yaml"))


def load_templates(path: str | Path | None = None) -> dict:
    """El YAML completo: `{version, default_for_days: {días: clave}, templates: {clave: {...}}}`."""
    with open(path or _templates_path(), encoding="utf-8") as f:
        data = yaml.safe_load(f)
    data["default_for_days"] = {int(k): v for k, v in (data.get("default_for_days") or {}).items()}
    return data


def create_program_from_template(db: Session, key: str | None, days_per_week: int, *,
                                 templates: dict | None = None, started_on: date | None = None) -> Program:
    """Crea el programa activo desde una plantilla (`key=None` → la de `default_for_days`) y archiva el
    anterior. La rotación arranca en 0."""
    data = templates or load_templates()
    dfd = data["default_for_days"]
    key = key or dfd[min(max(days_per_week, min(dfd)), max(dfd))]
    if key not in data["templates"]:
        raise KeyError(f"Plantilla desconocida: {key}")
    tpl = data["templates"][key]
    for old in db.scalars(select(Program).where(Program.status == "active")):
        old.status = "archived"
    prog = Program(
        name=tpl["name"], template_key=key, split_kind=tpl.get("split_kind", key), days_per_week=days_per_week,
        status="active", config=dict(tpl.get("config") or {}), created_by="template",
        started_on=started_on or local_today(), rotation_idx=0,
    )
    for i, d in enumerate(tpl["days"]):
        slots = [{"preferred_exercise_ids": [], **s} for s in d["slots"]]
        prog.days.append(ProgramDay(order_idx=i, name=d["name"], focus=list(d.get("focus") or []), slots=slots))
    db.add(prog)
    db.flush()
    return prog


# ── Historial ───────────────────────────────────────────────────────────────


def history_for(db: Session, exercise_id: str, equipment_profile_id: int | None = None,
                limit: int = 6) -> list[SessionPerf]:
    """Sesiones con ese ejercicio (sin calentamiento), la más reciente primero. Con `equipment_profile_id`
    se prefieren las de ese perfil; si nunca se hizo con él, se usan todas."""
    rows = db.execute(
        select(WorkoutSet, WorkoutSession.date, WorkoutSession.equipment_profile_id)
        .join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(WorkoutSet.exercise_id == exercise_id, WorkoutSet.is_warmup.is_(False))
        .order_by(WorkoutSession.date.desc(), WorkoutSession.id.desc(), WorkoutSet.set_idx, WorkoutSet.id)
    ).all()
    by_session: dict[int, SessionPerf] = {}
    for ws, d, eid in rows:
        perf = by_session.setdefault(ws.session_id, SessionPerf(d, [], eid))
        perf.sets.append(SetPerf(ws.reps, ws.weight_kg, ws.rir, ws.duration_s))
    sessions = list(by_session.values())
    if equipment_profile_id is not None:
        same = [s for s in sessions if s.equipment_profile_id == equipment_profile_id]
        sessions = same or sessions
    return sessions[:limit]


def _meta(ex: Exercise) -> dict:
    return {"equipment_tag": ex.equipment_tag, "load_kind": ex.load_kind or "total",
            "primary_muscle": ex.primary_muscle, "mechanic": ex.mechanic}


# ── Plan del día ────────────────────────────────────────────────────────────


def _equipment_profile(db: Session, on: date, eid: int | None, profile: Profile | None) -> EquipmentProfile | None:
    if eid is not None:
        return db.get(EquipmentProfile, eid)
    entry = (profile.schedule or {}).get(WEEKDAY_KEYS[on.weekday()]) if profile else None
    sched_id = entry.get("equipment_profile_id") if isinstance(entry, dict) else entry
    if isinstance(sched_id, int) and (ep := db.get(EquipmentProfile, sched_id)):
        return ep
    return db.scalars(
        select(EquipmentProfile).order_by(EquipmentProfile.is_default.desc(), EquipmentProfile.id)
    ).first()


def _match(ex: Exercise, slot: dict) -> int:
    """2 = patrón y músculo (o el único que pide el slot); 1 = solo patrón; 0 = solo músculo; -1 = nada."""
    p, m = slot.get("pattern"), slot.get("muscle")
    pm, mm = bool(p) and ex.movement_pattern == p, bool(m) and ex.primary_muscle == m
    if pm and (mm or not m) or (mm and not p):
        return 2
    return 1 if pm else 0 if mm else -1


def _candidates(db: Session, slot: dict, tags: frozenset[str] | set[str], used: set[str]) -> list[Exercise]:
    conds = []
    if slot.get("pattern"):
        conds.append(Exercise.movement_pattern == slot["pattern"])
    if slot.get("muscle"):
        conds.append(Exercise.primary_muscle == slot["muscle"])
    preferred = list(slot.get("preferred_exercise_ids") or [])
    if preferred:
        conds.append(Exercise.id.in_(preferred))
    if not conds:
        return []
    rows = db.scalars(select(Exercise).where(Exercise.enabled.is_(True), or_(*conds)))
    return [ex for ex in rows if ex.id not in used and eligible(ex, tags)]


LOADED_TAGS = {"barbell", "dumbbell", "machine", "cable", "smith", "sled", "ez_bar", "trap_bar", "kettlebell"}
BODYWEIGHT_OK_PATTERNS = {"pull_vertical", "core_flexion", "core_rotation", "core_stability"}


def _load_rank(ex: Exercise, slot: dict) -> int:
    """En slots de fuerza/hipertrofia, primero lo que admite sobrecarga progresiva con carga externa.
    Dominadas/fondos y core con peso corporal cuentan como cargables."""
    if ex.equipment_tag in LOADED_TAGS or ex.load_kind in ("bodyweight_plus", "assisted"):
        return 0
    if ex.equipment_tag == "bodyweight" and slot.get("pattern") in BODYWEIGHT_OK_PATTERNS:
        return 0
    return 1 if ex.equipment_tag in ("bodyweight", "band", "plate") else 2


def _oddity(ex: Exercise) -> int:
    """Rarezas que un entrenador no propondría por defecto: técnicos/avanzados no básicos y variantes
    duplicadas del dataset ("(variante 2)", "(vista lateral)")."""
    name = (ex.name_es or ex.name_en or "").lower()
    return int((ex.difficulty or 2) >= 3 and not ex.is_staple) + int("(variante" in name or "(vista" in name)


def rank_for_slot(db: Session, slot: dict, tags, *, used: set[str], history_ids: set[str],
                  recent_ids: set[str], injured: set[str], disliked: set[str],
                  recently_planned: set[str] = frozenset()) -> list[Exercise]:
    """Orden del planificador: sin lesión ni "no me gusta" (filtro con respaldo) > preferido del slot >
    calidad del encaje patrón/músculo > no planeado en los 3 días previos (variedad entre días A/B) >
    con historial > con carga externa > básico > no hecho en 48 h > nombre."""
    preferred = set(slot.get("preferred_exercise_ids") or [])

    def key(ex: Exercise):
        ok = ex.id not in disliked and not (set(ex.stress_joints or []) & injured)
        return (not ok, ex.id not in preferred, -_match(ex, slot), ex.id in recently_planned,
                ex.id not in history_ids, _oddity(ex), _load_rank(ex, slot), not ex.is_staple, ex.id in recent_ids,
                ex.display_name, ex.id)

    return sorted(_candidates(db, slot, tags, used), key=key)


def _recent_ids(db: Session, on: date) -> set[str]:
    rows = db.scalars(
        select(WorkoutSet.exercise_id).join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
        .where(WorkoutSession.date >= on - timedelta(days=RECENT_DAYS), WorkoutSession.date <= on)
    )
    return set(rows)


def _recently_planned(db: Session, on: date, days: int = 3) -> set[str]:
    rows = db.scalars(
        select(PlannedItem.exercise_id).join(PlannedSession, PlannedItem.planned_session_id == PlannedSession.id)
        .where(PlannedSession.date >= on - timedelta(days=days), PlannedSession.date < on,
               PlannedSession.status.notin_(("replaced",)))
    )
    return set(rows)


def _history_ids(db: Session) -> set[str]:
    return set(db.scalars(select(WorkoutSet.exercise_id).where(WorkoutSet.is_warmup.is_(False)).distinct()))


def _harder(chosen: Exercise, ranked: list[Exercise], slot: dict) -> Exercise | None:
    """Con techo de carga: una variante más exigente (unilateral o más difícil) con el mismo encaje."""
    for ex in ranked:
        if ex.id == chosen.id or _match(ex, slot) < _match(chosen, slot):
            continue
        uni = ex.laterality == "unilateral" and chosen.laterality != "unilateral"
        if uni or (ex.difficulty or 0) > (chosen.difficulty or 0):
            return ex
    return None


def build_session(db: Session, on: date, equipment_profile_id: int | None = None, *,
                  replace: bool = False, program_day_id: int | None = None) -> PlannedSession:
    """Plan del día `on` para el siguiente día de la rotación (no avanza `rotation_idx`). Si ya hay uno
    `proposed` para esa fecha lo devuelve; con `replace=True` lo marca `replaced` y crea otro."""
    existing = db.scalars(
        select(PlannedSession).where(PlannedSession.date == on, PlannedSession.status == "proposed")
        .order_by(PlannedSession.id.desc())
    ).first()
    if existing and not replace:
        return existing
    program = db.scalars(select(Program).where(Program.status == "active").order_by(Program.id.desc())).first()
    if program is None or not program.days:
        raise NoProgramError(_("No hay un programa activo: créalo desde una plantilla."))
    if existing:
        existing.status = "replaced"
    day = next((d for d in program.days if d.id == program_day_id), None) if program_day_id else None
    day = day or program.days[program.rotation_idx % len(program.days)]
    profile = db.get(Profile, 1) or db.scalars(select(Profile)).first()
    ep = _equipment_profile(db, on, equipment_profile_id, profile)
    tags = frozenset(ep.tags or []) if ep else ALL_TAGS
    loads = (ep.loads or {}) if ep else {}
    injured = active_injured_joints(profile.injuries if profile else [])
    disliked = set(profile.disliked_exercise_ids or []) if profile else set()
    history_ids, recent_ids = _history_ids(db), _recent_ids(db, on)
    recently_planned = _recently_planned(db, on)

    plan = PlannedSession(date=on, program_day_id=day.id, focus_label=day.name,
                          equipment_profile_id=ep.id if ep else None, status="proposed", source="planner")
    used: set[str] = set()
    missing: list[str] = []
    for slot in day.slots:
        ranked = rank_for_slot(db, slot, tags, used=used, history_ids=history_ids, recent_ids=recent_ids,
                               injured=injured, disliked=disliked, recently_planned=recently_planned)
        if not ranked:
            missing.append(slot.get("slot") or slot.get("pattern") or "?")
            continue
        ex, target = ranked[0], _target(db, ranked[0], slot, ep, loads)
        if target.load_ceiling and (alt := _harder(ex, ranked, slot)):
            ex, target = alt, _target(db, alt, slot, ep, loads)
            target.note = _("Variante más exigente por techo de carga. {note}", note=target.note)
        used.add(ex.id)
        plan.items.append(PlannedItem(
            order_idx=len(plan.items), slot=dict(slot), exercise_id=ex.id, sets=int(slot.get("sets", 3)),
            rep_min=target.rep_min, rep_max=target.rep_max, target_rir=int(target.target_rir or 2),
            target_weight_kg=target.weight_kg, target_note=target.note or None,
        ))
    if missing:
        plan.rationale = _("Sin ejercicio elegible para: {slots}", slots=", ".join(missing))
    db.add(plan)
    db.flush()
    return plan


def _target(db: Session, ex: Exercise, slot: dict, ep: EquipmentProfile | None, loads: dict) -> PlannedTarget:
    hist = history_for(db, ex.id, ep.id if ep else None)
    return next_target(hist, int(slot.get("rep_min", 8)), int(slot.get("rep_max", 12)),
                       slot.get("target_rir", 2), _meta(ex), loads)
