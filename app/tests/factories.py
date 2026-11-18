"""Fábricas de datos sintéticos (nunca datos reales) para las pruebas de entrenamiento."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fitos.core.models import EquipmentProfile, Exercise, Profile, WorkoutSession, WorkoutSet
from fitos.training.planner import ALL_TAGS

CASA_TAGS = ["bodyweight", "dumbbell", "bench_flat", "bench_incline"]


def make_exercise(db=None, id: str = "ex", *, name: str | None = None, muscle: str = "chest",
                  pattern: str = "push_horizontal", tag: str = "dumbbell", requires=(), station: str = "none",
                  mechanic: str = "compound", laterality: str = "bilateral", load_kind: str | None = None,
                  secondary=(), stress=(), staple: bool = False, difficulty: int = 2,
                  enabled: bool = True) -> Exercise:
    if load_kind is None:
        load_kind = {"dumbbell": "per_hand", "kettlebell": "per_hand", "bodyweight": "bodyweight"}.get(tag, "total")
    ex = Exercise(
        id=id, name_en=name or id, name_es=name or id, primary_muscle=muscle, secondary_muscles=list(secondary),
        equipment_tag=tag, requires=list(requires), station=station, movement_pattern=pattern, mechanic=mechanic,
        laterality=laterality, load_kind=load_kind, stress_joints=list(stress), is_staple=staple,
        difficulty=difficulty, enabled=enabled,
    )
    if db is not None:
        db.add(ex)
        db.flush()
    return ex


def make_profile(db, **kw) -> Profile:
    prof = Profile(id=1, days_per_week=kw.pop("days_per_week", 4), **kw)
    db.add(prof)
    db.flush()
    return prof


def make_equipment_profiles(db) -> tuple[EquipmentProfile, EquipmentProfile]:
    """Casa (peso corporal, mancuernas 2–24 de 2 en 2, banco plano e inclinado) y Gym (todo, por defecto)."""
    casa = EquipmentProfile(name="Casa", kind="home", is_default=False, tags=list(CASA_TAGS),
                            loads={"dumbbell_kg": list(range(2, 25, 2))})
    gym = EquipmentProfile(
        name="Gym", kind="gym", is_default=True, tags=sorted(ALL_TAGS),
        loads={"dumbbell_kg": [2.5 * i for i in range(1, 21)], "barbell_step_kg": 2.5, "machine_step_kg": 5,
               "cable_step_kg": 2.5, "plate_kg": [1.25, 2.5, 5, 10, 20]},
    )
    db.add_all([casa, gym])
    db.flush()
    return casa, gym


# (id, músculo, patrón, tag, requires, station, mechanic, extras)
_CATALOG = [
    # Casa
    ("goblet_squat", "quads", "squat", "dumbbell", [], "dumbbells", "compound", {"staple": True, "secondary": ["glutes"]}),
    ("db_bench", "chest", "push_horizontal", "dumbbell", ["bench_flat"], "dumbbells", "compound",
     {"staple": True, "secondary": ["triceps", "delts"], "stress": ["shoulder"]}),
    ("db_incline", "chest", "push_horizontal", "dumbbell", ["bench_incline"], "dumbbells", "compound",
     {"secondary": ["delts", "triceps"], "stress": ["shoulder"]}),
    ("pushup", "chest", "push_horizontal", "bodyweight", [], "none", "compound", {"secondary": ["triceps"]}),
    ("db_pullover", "lats", "pull_vertical", "dumbbell", ["bench_flat"], "dumbbells", "compound", {}),
    ("db_row", "upper_back", "pull_horizontal", "dumbbell", ["bench_flat"], "dumbbells", "compound",
     {"staple": True, "laterality": "unilateral", "secondary": ["lats", "biceps"]}),
    ("db_rdl", "hamstrings", "hinge", "dumbbell", [], "dumbbells", "compound",
     {"staple": True, "secondary": ["glutes", "lower_back"], "stress": ["lower_back"]}),
    ("db_lateral", "delts", "lateral_raise", "dumbbell", [], "dumbbells", "isolation", {"staple": True}),
    ("db_ohp", "delts", "push_vertical", "dumbbell", [], "dumbbells", "compound",
     {"staple": True, "secondary": ["triceps"], "stress": ["shoulder"]}),
    ("db_curl", "biceps", "elbow_flexion", "dumbbell", [], "dumbbells", "isolation", {"staple": True}),
    ("db_oh_ext", "triceps", "elbow_extension", "dumbbell", [], "dumbbells", "isolation", {"stress": ["elbow"]}),
    ("crunch", "abs", "core_flexion", "bodyweight", [], "none", "isolation", {"staple": True}),
    ("plank", "abs", "core_stability", "bodyweight", [], "none", "isolation", {"load_kind": "duration"}),
    ("db_lunge", "quads", "lunge", "dumbbell", [], "dumbbells", "compound",
     {"staple": True, "laterality": "unilateral", "secondary": ["glutes"], "stress": ["knee"]}),
    ("db_split_squat", "quads", "lunge", "dumbbell", ["bench_flat"], "dumbbells", "compound",
     {"laterality": "unilateral", "difficulty": 3, "secondary": ["glutes"]}),
    ("db_hip_thrust", "glutes", "hip_thrust", "dumbbell", ["bench_flat"], "dumbbells", "compound", {}),
    ("db_calf", "calves", "calf_raise", "dumbbell", [], "dumbbells", "isolation", {}),
    ("db_fly", "chest", "chest_fly", "dumbbell", ["bench_flat"], "dumbbells", "isolation", {}),
    ("nordic", "hamstrings", "knee_flexion", "bodyweight", [], "none", "isolation", {"difficulty": 3}),
    ("sissy_squat", "quads", "knee_extension", "bodyweight", [], "none", "isolation", {"stress": ["knee"]}),
    ("db_rear_fly", "delts", "rear_delt", "dumbbell", [], "dumbbells", "isolation", {}),
    # Gym
    ("bb_squat", "quads", "squat", "barbell", ["rack"], "squat_rack", "compound",
     {"staple": True, "secondary": ["glutes"], "stress": ["knee", "lower_back"]}),
    ("bb_bench", "chest", "push_horizontal", "barbell", ["bench_flat", "rack"], "bench_press_station", "compound",
     {"staple": True, "secondary": ["triceps", "delts"], "stress": ["shoulder"]}),
    ("pullup", "lats", "pull_vertical", "bodyweight", ["pullup_bar"], "pullup_bar", "compound",
     {"staple": True, "load_kind": "bodyweight_plus", "secondary": ["biceps"]}),
    ("lat_pulldown", "lats", "pull_vertical", "cable", [], "lat_pulldown", "compound",
     {"staple": True, "secondary": ["biceps"]}),
    ("cable_row", "upper_back", "pull_horizontal", "cable", [], "seated_row", "compound",
     {"staple": True, "secondary": ["lats", "biceps"]}),
    ("leg_press", "quads", "squat", "machine", [], "leg_press", "compound", {"staple": True, "secondary": ["glutes"]}),
    ("leg_ext", "quads", "knee_extension", "machine", [], "leg_extension", "isolation", {"staple": True}),
    ("leg_curl", "hamstrings", "knee_flexion", "machine", [], "leg_curl", "isolation", {"staple": True}),
    ("cable_fly", "chest", "chest_fly", "cable", [], "cable_tower", "isolation", {"staple": True}),
    ("pushdown", "triceps", "elbow_extension", "cable", [], "cable_tower", "isolation", {"staple": True}),
    ("bb_rdl", "hamstrings", "hinge", "barbell", [], "barbell_free", "compound",
     {"staple": True, "secondary": ["glutes", "lower_back"], "stress": ["lower_back"]}),
    ("dips", "triceps", "push_horizontal", "bodyweight", ["dip_bars"], "dip_bars", "compound",
     {"load_kind": "bodyweight_plus", "secondary": ["chest"]}),
    ("calf_machine", "calves", "calf_raise", "machine", [], "calf_machine", "isolation", {"staple": True}),
    ("disabled_press", "chest", "push_horizontal", "dumbbell", [], "dumbbells", "compound",
     {"enabled": False, "staple": True}),
]


def make_catalog(db) -> dict[str, Exercise]:
    out = {}
    for ex_id, muscle, pattern, tag, req, station, mech, extra in _CATALOG:
        out[ex_id] = make_exercise(db, ex_id, muscle=muscle, pattern=pattern, tag=tag, requires=req,
                                   station=station, mechanic=mech, **extra)
    return out


def log_session(db, on: date, exercise_id: str, sets: list[tuple], *, equipment_profile_id: int | None = None,
                warmups: int = 0, started_at: datetime | None = None, pr_flags: dict | None = None) -> WorkoutSession:
    """Registra una sesión con `sets` = [(reps, peso, rir), ...] de un ejercicio."""
    start = started_at or datetime(on.year, on.month, on.day, 11, 0, tzinfo=UTC)
    ws = WorkoutSession(date=on, started_at=start, last_activity_at=start, equipment_profile_id=equipment_profile_id,
                        state="completed")
    db.add(ws)
    db.flush()
    add_sets(db, ws, exercise_id, sets, warmups=warmups, pr_flags=pr_flags)
    return ws


def add_sets(db, ws: WorkoutSession, exercise_id: str, sets: list[tuple], *, warmups: int = 0,
             pr_flags: dict | None = None) -> None:
    idx = len(ws.sets)
    t = ws.started_at + timedelta(minutes=2 * idx)
    for _ in range(warmups):
        ws.sets.append(WorkoutSet(exercise_id=exercise_id, set_idx=idx, reps=10, weight_kg=10, is_warmup=True,
                                  logged_at=t))
        idx += 1
    for i, (reps, w, rir) in enumerate(sets):
        t = ws.started_at + timedelta(minutes=2 * (idx + 1))
        flags = (pr_flags or {}).get(i, [])
        ws.sets.append(WorkoutSet(exercise_id=exercise_id, set_idx=idx, reps=reps, weight_kg=w, rir=rir,
                                  logged_at=t, pr_flags=flags))
        idx += 1
    db.flush()
