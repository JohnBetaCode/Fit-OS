"""Consultas y acciones sobre el catálogo de ejercicios, y etiquetas reutilizables.

The label dicts keep Spanish values (the source language); English lives in `i18n/en_data.py` and
`tests/test_i18n_data.py` checks every value has it. Show them through `muscle_label()` and friends, or
`labels(MUSCLE_ES)` for a whole select."""

from __future__ import annotations

import re
from collections.abc import Iterable

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from ..core.models import Exercise, Profile
from ..i18n import _, lang

MUSCLE_ES = {
    "chest": "Pecho",
    "lats": "Dorsales",
    "upper_back": "Espalda alta",
    "traps": "Trapecios",
    "delts": "Hombros",
    "biceps": "Bíceps",
    "triceps": "Tríceps",
    "forearms": "Antebrazos",
    "abs": "Abdomen",
    "lower_back": "Zona lumbar",
    "quads": "Cuádriceps",
    "hamstrings": "Isquiotibiales",
    "glutes": "Glúteos",
    "calves": "Pantorrillas",
    "hips": "Cadera",
}

# Tags de equipo principal (`equipment_tag`) y de requisitos (`requires`).
EQUIPMENT_ES = {
    "bodyweight": "Peso corporal",
    "dumbbell": "Mancuernas",
    "cable": "Polea",
    "barbell": "Barra",
    "machine": "Máquina",
    "band": "Banda elástica",
    "smith": "Multipower (Smith)",
    "kettlebell": "Kettlebell",
    "plate": "Disco",
    "stability_ball": "Balón de estabilidad",
    "medicine_ball": "Balón medicinal",
    "bosu": "Bosu",
    "ez_bar": "Barra Z",
    "trap_bar": "Barra hexagonal",
    "assisted_machine": "Máquina asistida",
    "sled": "Trineo / prensa",
    "rope": "Cuerda",
    "foam_roller": "Rodillo de espuma",
    "ab_wheel": "Rueda abdominal",
    "cardio_machine": "Máquina de cardio",
    "other": "Otro",
    "bench_flat": "Banco plano",
    "bench_incline": "Banco inclinable",
    "pullup_bar": "Barra de dominadas",
    "dip_bars": "Paralelas",
    "rack": "Rack",
    "preacher_bench": "Banco Scott",
    "box": "Cajón",
}

PATTERN_ES = {
    "squat": "Sentadilla",
    "hinge": "Bisagra de cadera",
    "lunge": "Zancada",
    "push_horizontal": "Empuje horizontal",
    "push_vertical": "Empuje vertical",
    "pull_horizontal": "Tracción horizontal",
    "pull_vertical": "Tracción vertical",
    "chest_fly": "Aperturas",
    "lateral_raise": "Elevación lateral",
    "rear_delt": "Deltoide posterior",
    "elbow_flexion": "Flexión de codo",
    "elbow_extension": "Extensión de codo",
    "knee_extension": "Extensión de rodilla",
    "knee_flexion": "Flexión de rodilla",
    "hip_thrust": "Empuje de cadera",
    "hip_abduction": "Abducción de cadera",
    "hip_adduction": "Aducción de cadera",
    "calf_raise": "Elevación de talones",
    "core_flexion": "Core: flexión",
    "core_rotation": "Core: rotación",
    "core_stability": "Core: estabilidad",
    "carry": "Acarreo",
    "cardio": "Cardio",
    "mobility": "Movilidad",
    "other": "Otro",
}

JOINT_ES = {
    "shoulder": "Hombro",
    "elbow": "Codo",
    "wrist": "Muñeca",
    "lower_back": "Zona lumbar",
    "knee": "Rodilla",
    "hip": "Cadera",
    "ankle": "Tobillo",
    "neck": "Cuello",
}

MECHANIC_ES = {"compound": "Multiarticular", "isolation": "Monoarticular"}
LATERALITY_ES = {"bilateral": "Bilateral", "unilateral": "Unilateral"}
LABEL_TABLES = {
    "muscle": MUSCLE_ES, "equipment": EQUIPMENT_ES, "pattern": PATTERN_ES, "joint": JOINT_ES,
    "mechanic": MECHANIC_ES, "laterality": LATERALITY_ES,
}


def _label(table: dict[str, str], key: str | None) -> str:
    return _(table[key]) if key in table else (key or "")


def labels(table: dict[str, str]) -> dict[str, str]:
    """A label dict in the current language, same keys and order: `labels(MUSCLE_ES)`."""
    return {k: _(v) for k, v in table.items()}


def muscle_label(key: str | None) -> str:
    """`chest` → "Pecho" / "Chest"; an unknown key comes back as is."""
    return _label(MUSCLE_ES, key)


def equipment_label(key: str | None) -> str:
    return _label(EQUIPMENT_ES, key)


def pattern_label(key: str | None) -> str:
    return _label(PATTERN_ES, key)


def joint_label(key: str | None) -> str:
    return _label(JOINT_ES, key)


def mechanic_label(key: str | None) -> str:
    return _label(MECHANIC_ES, key)


def laterality_label(key: str | None) -> str:
    return _label(LATERALITY_ES, key)


def instructions(exercise: Exercise) -> list[str]:
    """Technique steps in the current language (same as `Exercise.instructions`)."""
    return exercise.instructions

# Vocales con y sin tilde se vuelven comodín de LIKE: "jalon" encuentra "jalón".
_FOLD = re.compile(r"[aeiouáéíóúü]")


def _like(word: str) -> str:
    word = word.replace("\\", "").replace("%", "").replace("_", "")
    return f"%{_FOLD.sub('_', word.lower())}%"


def eligible(exercise: Exercise, tags: Iterable[str]) -> bool:
    """Un ejercicio es elegible para un perfil si su equipo y todos sus requisitos están en los tags."""
    tags = set(tags)
    return exercise.equipment_tag in tags and set(exercise.requires or []) <= tags


def search(
    db: Session,
    q: str | None = None,
    muscle: str | None = None,
    equipment_tag: str | None = None,
    profile_tags: Iterable[str] | None = None,
    pattern: str | None = None,
    staples_only: bool = False,
    favorites_only: bool = False,
    limit: int = 48,
    offset: int = 0,
    include_disabled: bool = False,
) -> list[Exercise]:
    """Búsqueda paginada. Orden: básicos primero, luego por nombre visible."""
    stmt = select(Exercise)
    if not include_disabled:
        stmt = stmt.where(Exercise.enabled.is_(True))
    for word in (q or "").split():
        pat = _like(word)
        if pat != "%%":
            stmt = stmt.where(
                or_(func.lower(Exercise.name_es).like(pat), func.lower(Exercise.name_en).like(pat))
            )
    if muscle:
        stmt = stmt.where(Exercise.primary_muscle == muscle)
    if equipment_tag:
        stmt = stmt.where(Exercise.equipment_tag == equipment_tag)
    if profile_tags is not None:
        tags = sorted(set(profile_tags))
        req = func.json_each(Exercise.requires).table_valued("value")
        stmt = stmt.where(
            Exercise.equipment_tag.in_(tags),
            ~exists(select(1).select_from(req).where(req.c.value.not_in(tags))),
        )
    if pattern:
        stmt = stmt.where(Exercise.movement_pattern == pattern)
    if staples_only:
        stmt = stmt.where(Exercise.is_staple.is_(True))
    if favorites_only:
        stmt = stmt.where(Exercise.favorite.is_(True))
    name = Exercise.name_en if lang() == "en" else func.coalesce(Exercise.name_es, Exercise.name_en)
    stmt = stmt.order_by(Exercise.is_staple.desc(), name, Exercise.id).limit(limit).offset(offset)
    return list(db.scalars(stmt))


def get(db: Session, exercise_id: str) -> Exercise | None:
    return db.get(Exercise, exercise_id)


def any_enabled(db: Session) -> bool:
    return db.scalar(select(exists().where(Exercise.enabled.is_(True)))) or False


def toggle_favorite(db: Session, exercise_id: str) -> bool:
    """Invierte `favorite` y devuelve el estado nuevo."""
    ex = db.get(Exercise, exercise_id)
    if ex is None:
        raise KeyError(exercise_id)
    ex.favorite = not ex.favorite
    db.flush()
    return ex.favorite


def _profile(db: Session) -> Profile:
    prof = db.get(Profile, 1)
    if prof is None:
        prof = Profile(id=1)
        db.add(prof)
        db.flush()
    return prof


def disliked_ids(db: Session) -> set[str]:
    prof = db.get(Profile, 1)
    return set(prof.disliked_exercise_ids or []) if prof else set()


def toggle_disliked(db: Session, exercise_id: str) -> bool:
    """Agrega o quita el ejercicio de "no me lo propongas"; devuelve True si quedó descartado."""
    if db.get(Exercise, exercise_id) is None:
        raise KeyError(exercise_id)
    prof = _profile(db)
    ids = list(prof.disliked_exercise_ids or [])
    disliked = exercise_id not in ids
    ids = [*ids, exercise_id] if disliked else [i for i in ids if i != exercise_id]
    prof.disliked_exercise_ids = ids  # lista nueva: SQLAlchemy no detecta mutaciones del JSON
    db.flush()
    return disliked
