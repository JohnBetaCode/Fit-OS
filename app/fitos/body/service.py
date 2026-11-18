"""Perfil del usuario, perfiles de equipo y medidas corporales."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import BodyMetric, EquipmentProfile, Profile
from ..core.timeutil import age_on, local_today
from ..i18n import _

ALL_TAGS = [
    "bodyweight", "dumbbell", "cable", "barbell", "machine", "band", "smith", "kettlebell", "plate",
    "stability_ball", "medicine_ball", "bosu", "ez_bar", "trap_bar", "assisted_machine", "sled", "rope",
    "foam_roller", "ab_wheel", "cardio_machine",
    "bench_flat", "bench_incline", "bench_decline", "pullup_bar", "dip_bars", "rack", "preacher_bench", "box",
]
EQUIPMENT_TAGS = ALL_TAGS[:20]
REQUIRE_TAGS = ALL_TAGS[20:]

TAG_ES = {
    "bodyweight": "Peso corporal", "dumbbell": "Mancuernas", "cable": "Poleas", "barbell": "Barra olímpica",
    "machine": "Máquinas de palanca", "band": "Bandas elásticas", "smith": "Máquina Smith", "kettlebell": "Kettlebell",
    "plate": "Discos / lastre", "stability_ball": "Balón de estabilidad", "medicine_ball": "Balón medicinal",
    "bosu": "Bosu", "ez_bar": "Barra Z", "trap_bar": "Barra hexagonal", "assisted_machine": "Máquina asistida",
    "sled": "Prensa / trineo", "rope": "Cuerda", "foam_roller": "Rodillo", "ab_wheel": "Rueda abdominal",
    "cardio_machine": "Máquinas de cardio", "bench_flat": "Banco plano", "bench_incline": "Banco inclinado",
    "bench_decline": "Banco declinado", "pullup_bar": "Barra de dominadas", "dip_bars": "Paralelas",
    "rack": "Rack", "preacher_bench": "Banco Scott", "box": "Cajón",
}

GOALS_ES = {
    "hypertrophy": "Hipertrofia (ganar músculo)", "strength": "Fuerza", "fat_loss": "Perder grasa",
    "recomp": "Recomposición", "health": "Salud general",
}
EXPERIENCE_ES = {"beginner": "Principiante", "intermediate": "Intermedio", "advanced": "Avanzado"}
WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS_ES = {"mon": "Lunes", "tue": "Martes", "wed": "Miércoles", "thu": "Jueves", "fri": "Viernes",
               "sat": "Sábado", "sun": "Domingo"}
JOINTS_ES = {"shoulder": "Hombro", "elbow": "Codo", "wrist": "Muñeca", "lower_back": "Espalda baja", "knee": "Rodilla",
             "hip": "Cadera", "ankle": "Tobillo", "neck": "Cuello"}
SEX_ES = {"male": "Hombre", "female": "Mujer"}
METRIC_ES = {"weight_kg": "Peso", "body_fat_pct": "Grasa corporal", "waist_cm": "Cintura", "chest_cm": "Pecho",
             "arm_cm": "Brazo", "hip_cm": "Cadera", "thigh_cm": "Muslo", "neck_cm": "Cuello"}
METRIC_UNITS = {"weight_kg": "kg", "body_fat_pct": "%", "waist_cm": "cm", "chest_cm": "cm", "arm_cm": "cm",
                "hip_cm": "cm", "thigh_cm": "cm", "neck_cm": "cm"}
# Tape measurements shown in Progress, in this order (weight has its own card)
MEASUREMENTS = ["waist_cm", "chest_cm", "arm_cm", "hip_cm", "thigh_cm", "neck_cm", "body_fat_pct"]
METRIC_BOUNDS = {"weight_kg": (30, 250), "body_fat_pct": (3, 60), "waist_cm": (40, 200), "chest_cm": (50, 200),
                 "arm_cm": (15, 70), "hip_cm": (50, 200), "thigh_cm": (25, 100), "neck_cm": (20, 70)}
# WhatsApp words for each measurement (normalized, both languages)
MEASURE_WORDS = {
    "cintura": "waist_cm", "waist": "waist_cm", "pecho": "chest_cm", "chest": "chest_cm", "brazo": "arm_cm",
    "arm": "arm_cm", "biceps": "arm_cm", "cadera": "hip_cm", "hip": "hip_cm", "hips": "hip_cm", "muslo": "thigh_cm",
    "pierna": "thigh_cm", "thigh": "thigh_cm", "cuello": "neck_cm", "neck": "neck_cm", "grasa": "body_fat_pct",
    "body fat": "body_fat_pct", "bodyfat": "body_fat_pct", "fat": "body_fat_pct", "bf": "body_fat_pct",
}
# Default equipment profile names (created in Spanish; shown through `equipment_name`).
DEFAULT_PROFILE_NAMES = ("Gym", "Casa")

# Every dict above keeps Spanish values (the source language); the English lives in `i18n/en_data.py` and
# `tests/test_i18n_data.py` checks that each value has it. Show them through these helpers.
LABEL_TABLES = {
    "tag": TAG_ES, "goal": GOALS_ES, "experience": EXPERIENCE_ES, "weekday": WEEKDAYS_ES, "joint": JOINTS_ES,
    "sex": SEX_ES, "metric": METRIC_ES,
}


def label(table: str, key: str | None) -> str:
    """`label("goal", "recomp")` → "Recomposición" / "Recomposition"; an unknown key comes back as is."""
    values = LABEL_TABLES[table]
    return _(values[key]) if key in values else (key or "")


def labels(table: str) -> dict[str, str]:
    """The whole table in the current language, same keys and order (for selects in templates)."""
    return {k: _(v) for k, v in LABEL_TABLES[table].items()}


def tag_label(key: str | None) -> str:
    return label("tag", key)


def goal_label(key: str | None) -> str:
    return label("goal", key)


def experience_label(key: str | None) -> str:
    return label("experience", key)


def weekday_label(key: str | None) -> str:
    return label("weekday", key)


def joint_label(key: str | None) -> str:
    return label("joint", key)


def sex_label(key: str | None) -> str:
    return label("sex", key)


def metric_label(kind: str | None) -> str:
    return label("metric", kind)


def equipment_name(ep: EquipmentProfile | str | None) -> str:
    """Name of an equipment profile for display: the defaults ("Casa") are translated, the user's own names
    are shown as typed."""
    name = ep if isinstance(ep, str) or ep is None else ep.name
    return _(name) if name in DEFAULT_PROFILE_NAMES else (name or "")


def default_profiles() -> list[dict]:
    return [
        {
            "name": "Gym", "kind": "gym", "is_default": True, "tags": list(ALL_TAGS),
            "loads": {"dumbbell_kg": [x / 2 for x in range(5, 101, 5)], "barbell_step_kg": 2.5,
                      "machine_step_kg": 5, "cable_step_kg": 2.5, "kettlebell_kg": [8, 12, 16, 20, 24, 28, 32]},
        },
        {
            "name": "Casa", "kind": "home", "is_default": False,
            "tags": ["bodyweight", "dumbbell", "bench_flat", "bench_incline"],
            "loads": {"dumbbell_kg": list(range(2, 25, 2))},
        },
    ]


def ensure_defaults(db: Session) -> Profile:
    """Crea el perfil (fila 1) y los perfiles de equipo Gym/Casa si no existen. Idempotente."""
    prof = db.get(Profile, 1)
    if prof is None:
        prof = Profile(id=1, schedule={}, injuries=[], disliked_exercise_ids=[])
        db.add(prof)
    if db.scalar(select(EquipmentProfile.id).limit(1)) is None:
        for p in default_profiles():
            db.add(EquipmentProfile(**p))
    db.flush()
    if not prof.schedule:
        gym = db.scalar(select(EquipmentProfile).where(EquipmentProfile.is_default.is_(True)))
        gid = gym.id if gym else None
        prof.schedule = {d: {"equipment_profile_id": gid, "time": "06:30"} for d in ("mon", "tue", "thu", "fri")}
    return prof


def get_profile(db: Session) -> Profile:
    return db.get(Profile, 1) or ensure_defaults(db)


def age(prof: Profile, on: date | None = None) -> int | None:
    return age_on(prof.birth_date, on or local_today()) if prof.birth_date else None


def equipment_profiles(db: Session) -> list[EquipmentProfile]:
    return list(db.scalars(select(EquipmentProfile).order_by(EquipmentProfile.is_default.desc(), EquipmentProfile.name)))


def default_equipment(db: Session) -> EquipmentProfile | None:
    return db.scalar(select(EquipmentProfile).where(EquipmentProfile.is_default.is_(True))) or db.scalar(
        select(EquipmentProfile).limit(1)
    )


def equipment_by_name(db: Session, word: str) -> EquipmentProfile | None:
    w = word.strip().lower()
    for ep in equipment_profiles(db):
        if ep.name.lower() == w or ep.kind == w or (w in ("casa", "home") and ep.kind == "home") or (
            w in ("gym", "gimnasio") and ep.kind == "gym"
        ):
            return ep
    return None


def latest_metric(db: Session, kind: str = "weight_kg") -> BodyMetric | None:
    return db.scalar(select(BodyMetric).where(BodyMetric.kind == kind).order_by(BodyMetric.measured_on.desc()).limit(1))


def log_metric(db: Session, kind: str, value: float, on: date | None = None, source: str = "manual") -> BodyMetric:
    lo, hi = METRIC_BOUNDS.get(kind, (0, 1000))
    if not lo <= value <= hi:
        raise ValueError(_("valor fuera de rango para {kind}: {value}", kind=metric_label(kind), value=value))
    on = on or local_today()
    row = db.scalar(select(BodyMetric).where(BodyMetric.kind == kind, BodyMetric.measured_on == on,
                                             BodyMetric.source == source))
    if row:
        row.value = value
    else:
        row = BodyMetric(kind=kind, value=value, measured_on=on, source=source)
        db.add(row)
    return row


def metric_series(db: Session, kind: str = "weight_kg", limit: int = 365) -> list[BodyMetric]:
    rows = db.scalars(select(BodyMetric).where(BodyMetric.kind == kind).order_by(BodyMetric.measured_on.desc()).limit(limit))
    return list(reversed(list(rows)))


def import_profile_yaml(db: Session, path: Path) -> Profile:
    data = yaml.safe_load(Path(path).read_text()) or {}
    prof = ensure_defaults(db)
    if data.get("sex"):
        prof.sex = str(data["sex"]).lower()
    if data.get("birth_date"):
        bd = data["birth_date"]
        prof.birth_date = bd if isinstance(bd, date) else date.fromisoformat(str(bd))
    if data.get("height_cm"):
        prof.height_cm = float(data["height_cm"])
    if data.get("activity") and not prof.goal_notes:
        prof.goal_notes = str(data["activity"])
    if data.get("weight_kg"):
        wd = data.get("weight_date")
        on = wd if isinstance(wd, date) else (date.fromisoformat(str(wd)) if wd else local_today())
        log_metric(db, "weight_kg", float(data["weight_kg"]), on, source="import")
    return prof


def injured_joints(prof: Profile) -> set[str]:
    return {i.get("area") for i in prof.injuries or [] if i.get("active", True) and i.get("area")}


def latest_by_kind(db: Session, kinds: list[str]) -> dict[str, dict]:
    """For each kind: latest value, its date, and the change against the previous measurement."""
    out = {}
    for k in kinds:
        rows = db.scalars(select(BodyMetric).where(BodyMetric.kind == k).order_by(BodyMetric.measured_on.desc())
                          .limit(2)).all()
        if rows:
            out[k] = {"value": rows[0].value, "on": rows[0].measured_on,
                      "delta": round(rows[0].value - rows[1].value, 1) if len(rows) > 1 else None}
    return out
