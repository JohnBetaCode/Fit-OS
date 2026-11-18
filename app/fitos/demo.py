"""Synthetic demo data: `fitos demo seed` fills an empty database so every dashboard tab has something to show.

Everything here is invented (no real person, no real health data). Copy that ends up stored is Spanish (the
source language) and is translated on display like the rest of the data: food guidance items, the lab name and
the target label have English entries in `i18n/en_data.py` (see `labs.service.foods`); the profile's free-text
goal is written in the language active when seeding. Training goes through the real services
(planner → start_session → log_sets → finish_session), so e1RM, PRs, rotation and metrics behave exactly as in
normal use; the demo only decides how many reps the simulated lifter manages. Deterministic for a given seed
and `today`."""

from __future__ import annotations

import math
import random
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .body import service as body
from .core.models import (
    AuditLog,
    Biomarker,
    BiomarkerNote,
    BiomarkerRange,
    BodyMetric,
    EquipmentProfile,
    Exercise,
    JobRun,
    LabReport,
    LabResult,
    PlannedItem,
    PlannedSession,
    Program,
    ProgramDay,
    WaMessage,
    WorkoutSession,
    WorkoutSet,
)
from .core.timeutil import local_today, local_tz
from .i18n import _
from .labs import service as labs
from .training import progression
from .training import service as tr
from .wa.parser import SetEntry

DEMO_SOURCE = "demo"
DEMO_NOTE_PREFIX = "[demo]"
DEMO_NOTE_RAW = "[demo] pautas genéricas de ejemplo"
DEMO_LAB_NAME = "Laboratorio Demo"
BODYWEIGHT_KG = 81.0  # used to load bodyweight_plus exercises (pull-ups, dips) in the simulation
MISS_RATE = 0.08
FATIGUE_REPS = 0.5  # reps lost per extra set of the same exercise


class DemoError(RuntimeError):
    """The demo cannot be seeded (data already present, empty catalog, missing biomarker dictionary)."""


@dataclass
class DemoSummary:
    sessions: int = 0
    missed: int = 0
    sets: int = 0
    pr_sets: int = 0
    weight_increases: int = 0
    body_metrics: int = 0
    lab_reports: int = 0
    lab_results: int = 0
    today_plan: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


# ── Guards ──────────────────────────────────────────────────────────────────


def has_user_data(db: Session) -> bool:
    return (db.scalar(select(WorkoutSession.id).limit(1)) is not None
            or db.scalar(select(LabResult.id).limit(1)) is not None)


def wipe(db: Session) -> None:
    """Delete training, body and lab data (plus demo targets/notes) so the demo starts from a clean slate."""
    for model in (WorkoutSet, WorkoutSession, PlannedItem, PlannedSession, ProgramDay, Program, BodyMetric,
                  LabResult, LabReport):
        db.execute(delete(model))
    db.execute(delete(BiomarkerRange).where(BiomarkerRange.source == DEMO_SOURCE))
    db.execute(delete(BiomarkerNote).where(BiomarkerNote.raw.like(f"{DEMO_NOTE_PREFIX}%")))
    db.flush()


# ── Profile and body ────────────────────────────────────────────────────────


def _setup_profile(db: Session, today: date) -> EquipmentProfile:
    prof = body.ensure_defaults(db)
    gym = body.equipment_by_name(db, "gym") or body.default_equipment(db)
    if gym is None:
        raise DemoError(_("No hay perfiles de equipo"))
    prof.display_name = "Demo"
    prof.sex = "male"
    prof.birth_date = date(today.year - 34, 3, 14)
    prof.height_cm = 178.0
    prof.experience = "intermediate"
    prof.primary_goal = "recomp"
    prof.goal_notes = _("Perfil de demostración: bajar un poco de grasa sin perder fuerza.")
    prof.days_per_week = 4
    prof.session_minutes = 60
    prof.schedule = {d: {"equipment_profile_id": gym.id, "time": "06:30"} for d in ("mon", "tue", "thu", "fri")}
    prof.injuries = []
    prof.disliked_exercise_ids = []
    prof.onboarded = True
    db.flush()
    return gym


def _seed_body(db: Session, rng: random.Random, start: date, today: date) -> int:
    """Morning weigh-ins on most days, trending ~1.5 kg down; waist every week."""
    days = (today - start).days
    n = 0
    for i in range(days + 1):
        d = start + timedelta(days=i)
        frac = i / max(days, 1)
        if d != today and rng.random() < 0.2:
            continue
        w = 82.4 - 1.5 * frac + 0.25 * math.sin(i / 3.1) + rng.gauss(0, 0.25)
        body.log_metric(db, "weight_kg", round(w, 1), on=d)
        n += 1
        if i % 7 == 0:
            body.log_metric(db, "waist_cm", round(88.0 - 1.8 * frac + rng.gauss(0, 0.3), 1), on=d)
            n += 1
    return n


# ── Training simulation ─────────────────────────────────────────────────────

_BAR = {"barbell", "smith", "ez_bar", "trap_bar"}
_GUESS_COMPOUND = {  # first-session working weight (kg; per hand for dumbbells)
    "bar": {"quads": 80, "hamstrings": 85, "glutes": 85, "chest": 65, "delts": 42.5, "upper_back": 60, "lats": 55},
    "dumbbell": {"chest": 26, "delts": 18, "upper_back": 28, "lats": 20, "quads": 22, "hamstrings": 26,
                 "glutes": 24},
    "machine": {"quads": 140, "glutes": 100, "hamstrings": 90},
    "cable": {"triceps": 30},
}
_GUESS_ISOLATION = {
    "bar": {"biceps": 30, "triceps": 25},
    "dumbbell": {"biceps": 12, "triceps": 14, "delts": 10, "chest": 12, "calves": 20},
    "machine": {"quads": 45, "hamstrings": 40, "calves": 70},
    "cable": {"triceps": 25, "biceps": 22.5, "delts": 10, "chest": 17.5},
}
_GUESS_DEFAULT = {"bar": 40, "dumbbell": 12, "machine": 50, "cable": 40, "kettlebell": 16}


def _family(tag: str | None) -> str:
    if tag in _BAR:
        return "bar"
    if tag in ("machine", "sled", "assisted_machine"):
        return "machine"
    if tag in ("cable", "rope"):
        return "cable"
    return tag or "other"


def _guess_weight(ex: Exercise, loads: dict) -> float | None:
    fam = _family(ex.equipment_tag)
    table = _GUESS_COMPOUND if ex.mechanic == "compound" else _GUESS_ISOLATION
    w = table.get(fam, {}).get(ex.primary_muscle or "")
    if w is None:
        w = _GUESS_DEFAULT.get(fam, 20)
        if ex.mechanic == "compound":
            w *= 1.6
    return progression.round_to_available(float(w), ex.load_kind or "total", ex.equipment_tag, loads, "nearest")


@dataclass
class _Strength:
    """Hidden capacity of one exercise: Epley e1RM (on total load) at `week0`, growing linearly per week."""

    e1rm0: float
    week0: float
    growth: float

    def at(self, week: float, form: float) -> float:
        return self.e1rm0 * (1 + self.growth * (week - self.week0)) * form


def _loaded(ex: Exercise) -> bool:
    return (ex.load_kind or "total") not in ("bodyweight", "duration", "bodyweight_plus", "assisted")


def _simulate_item(rng: random.Random, ex: Exercise, item: PlannedItem, state: dict, week: float, form: float,
                   loads: dict) -> list[SetEntry]:
    lk = ex.load_kind or "total"
    rir_t = item.target_rir if item.target_rir is not None else 2
    lo, hi = item.rep_min, item.rep_max
    if lk == "duration":
        base = state.setdefault(ex.id, 40 + rng.randint(-5, 5))
        cap = base + 1.5 * week
        return [SetEntry(reps=None, weight_kg=None, duration_s=int(round(cap * form - 4 * i, -1) or 20))
                for i in range(item.sets)]
    if _loaded(ex):
        weight = item.target_weight_kg or _guess_weight(ex, loads) or 10.0
        total = weight
    else:  # bodyweight, bodyweight_plus (added load), assisted (kept as-is)
        weight = item.target_weight_kg if lk == "bodyweight_plus" else None
        total = BODYWEIGHT_KG + (weight or 0)
    if ex.id not in state:
        # Calibrate so the first session lands ~2 reps above the bottom of the range at the target RIR.
        growth = 0.012 if ex.mechanic == "compound" else 0.009
        state[ex.id] = _Strength(total * (1 + (lo + 2 + rir_t) / 30), week, growth)
    strength: _Strength = state[ex.id]
    e1rm_today = strength.at(week, form)
    out = []
    for i in range(item.sets):
        to_failure = 30 * (e1rm_today / total - 1) - FATIGUE_REPS * i
        reps = round(to_failure - rir_t + rng.uniform(-0.6, 0.6))
        floor = lo - 1 if i == item.sets - 1 and i > 0 else lo  # after a load jump the lifter grinds out rep_min
        reps = int(min(hi, max(floor, reps, 1)))
        rir = float(min(4, max(0, round(to_failure - reps))))
        out.append(SetEntry(reps=reps, weight_kg=weight, rir=rir))
    return out


def _local_dt(d: date, hh: int, mm: int) -> datetime:
    return datetime.combine(d, time(hh, mm), tzinfo=local_tz())


def _run_session(db: Session, rng: random.Random, plan: PlannedSession, gym: EquipmentProfile, state: dict,
                 week: float, summary: DemoSummary) -> None:
    ws = tr.start_session(db, plan, source=DEMO_SOURCE)
    form = rng.gauss(1.0, 0.012)
    loads = gym.loads or {}
    last_weight: dict[str, float] = state.setdefault("_last_weight", {})
    for n in range(len(plan.items)):
        cur = tr.current_item(db, ws)
        if cur is None:
            break
        last = n == len(plan.items) - 1
        if last and rng.random() < 0.08:  # short on time: drop the last accessory now and then
            tr.advance(db, ws, status="skipped")
            continue
        # Counted from the numbers, not from the (translated) target note.
        prev, tw = last_weight.get(cur.exercise_id), cur.target_weight_kg
        if prev is not None and tw is not None and tw > prev + 1e-9:
            summary.weight_increases += 1
        if tw is not None:
            last_weight[cur.exercise_id] = tw
        entries = _simulate_item(rng, cur.exercise, cur, state, week, form, loads)
        tr.log_sets(db, ws, cur, entries, raw_text="", actor="system")
        tr.advance(db, ws)
    tr.finish_session(db, ws, rpe=rng.choice([7, 7, 8, 8, 8, 9]))
    # Move every timestamp to the (past) training morning instead of "now".
    start = _local_dt(plan.date, 6, 30) + timedelta(minutes=rng.randint(-10, 25))
    t = start
    db.refresh(ws)
    for st in ws.sets:
        t += timedelta(seconds=rng.randint(130, 240))
        st.logged_at = t
    ws.started_at = start
    ws.last_activity_at = ws.ended_at = t + timedelta(minutes=3)
    db.flush()
    summary.sessions += 1
    summary.sets += len(ws.sets)
    summary.pr_sets += sum(1 for s in ws.sets if s.pr_flags)


def _seed_training(db: Session, rng: random.Random, gym: EquipmentProfile, start: date, today: date,
                   summary: DemoSummary) -> None:
    prog = tr.ensure_program(db)
    prog.started_on = start
    state: dict = {}
    for i in range((today - start).days):
        d = start + timedelta(days=i)
        if not tr.is_training_day(db, d):
            continue
        plan = tr.get_or_build_plan(db, d, gym.id)
        if (today - d).days > 7 and rng.random() < MISS_RATE:
            tr.skip_today(db, d)  # missed day: the rotation does not advance
            summary.missed += 1
            continue
        _run_session(db, rng, plan, gym, state, i / 7, summary)
    if tr.is_training_day(db, today):
        tr.get_or_build_plan(db, today, gym.id)
        summary.today_plan = True


# ── Labs ────────────────────────────────────────────────────────────────────

# Values per report (oldest → newest); None = not measured in that report. Canonical units of biomarkers.yaml.
# Latest report is mostly in range with LDL and vitamin D borderline and ApoB above the personal target.
LAB_VALUES: dict[str, tuple[float | None, float | None, float | None]] = {
    "ldl_c": (128, 121, 114),
    "hdl_c": (44, 47, 51),
    "triglycerides": (138, 121, 104),
    "total_cholesterol": (199, 192, 186),
    "apob": (92, 88, 84),
    "glucose_fasting": (94, 91, 88),
    "insulin_fasting": (9.8, 8.1, 6.9),
    "hba1c": (5.4, 5.3, 5.2),
    "alt": (34, 29, 26),
    "ast": (27, 25, 24),
    "creatinine": (1.02, 1.05, 1.08),
    "cystatin_c": (0.82, 0.80, 0.81),
    "uric_acid": (6.1, 5.8, 5.5),
    "testosterone_total": (512, 548, 590),
    "estradiol": (None, 27, 29),
    "fsh": (None, 4.1, None),
    "lh": (None, 4.6, None),
    "tsh": (2.1, 1.8, 2.3),
    "vitamin_d_25oh": (19, 24, 27),
    "vitamin_b12": (410, 455, 480),
    "ferritin": (145, 132, 120),
    "hs_crp": (1.4, 2.2, 1.1),
    "homocysteine": (11.2, 10.1, 9.4),
}
LAB_DAYS_AGO = (545, 270, 21)
DEMO_TARGETS = {"apob": (None, 80.0, "< 80 (meta personal)")}
DEMO_NOTES = {
    "ldl_c": (["Avena y cebada (betaglucanos)", "Legumbres", "Nueces y almendras", "Aceite de oliva extra virgen"],
              ["Embutidos y carnes procesadas", "Mantequilla y grasas saturadas", "Bollería industrial"]),
    "apob": (["Fibra soluble (avena, psyllium)", "Pescado azul", "Frutos secos"],
             ["Grasas trans", "Ultraprocesados", "Exceso de azúcares añadidos"]),
    "vitamin_d_25oh": (["Pescado azul (salmón, sardina)", "Huevos", "Sol breve y regular"], ["Alcohol en exceso"]),
    "triglycerides": (["Pescado azul", "Verduras y legumbres"], ["Bebidas azucaradas", "Alcohol"]),
}


def _seed_labs(db: Session, today: date, summary: DemoSummary) -> None:
    if not labs.ensure_biomarkers(db):
        raise DemoError(_("No encuentro catalog/biomarkers.yaml: no puedo crear los exámenes de demostración"))
    for key, (low, high, label) in DEMO_TARGETS.items():
        if db.scalar(select(BiomarkerRange.id).where(BiomarkerRange.biomarker_key == key,
                                                     BiomarkerRange.kind == "user_target")) is None:
            db.add(BiomarkerRange(biomarker_key=key, kind="user_target", sex="any", low=low, high=high, label=label,
                                  source=DEMO_SOURCE))
    for key, (pos, neg) in DEMO_NOTES.items():
        if db.get(BiomarkerNote, key) is None:
            db.add(BiomarkerNote(biomarker_key=key, food_positive=pos, food_negative=neg,
                                 raw=DEMO_NOTE_RAW))
    for idx, ago in enumerate(LAB_DAYS_AGO):
        on = today - timedelta(days=ago)
        rep = LabReport(collected_on=on, date_precision="day", lab_name=DEMO_LAB_NAME, source="manual",
                        status="confirmed")
        db.add(rep)
        db.flush()
        for key, values in LAB_VALUES.items():
            v = values[idx]
            bm = db.get(Biomarker, key)
            if v is None or bm is None:
                continue
            db.add(LabResult(report_id=rep.id, biomarker_key=key, collected_on=on, value_num=v, unit=bm.canonical_unit,
                             value_canonical=labs.to_canonical(bm, bm.canonical_unit, v), raw_name=bm.name_es))
        labs.recompute_derived(db, rep)
        summary.lab_reports += 1
    summary.lab_results = db.scalar(select(func.count()).select_from(LabResult)) or 0


# ── Entry point ─────────────────────────────────────────────────────────────


def seed_demo(db: Session, *, today: date | None = None, weeks: int = 10, seed: int = 7,
              force: bool = False) -> DemoSummary:
    """Fill the database with a synthetic demo user: profile, `weeks` of training, body weight and 3 lab reports.

    Refuses (DemoError) if there are workout sessions or lab results already, unless `force`, which wipes them
    first. Needs the exercise catalog. Leaves no outgoing WhatsApp message pending and marks today's morning
    plan job as done so the scheduler does not announce the demo plan."""
    if weeks < 1:
        raise DemoError(_("weeks debe ser ≥ 1"))
    if db.scalar(select(Exercise.id).where(Exercise.enabled.is_(True)).limit(1)) is None:
        raise DemoError(_("El catálogo de ejercicios está vacío: impórtalo primero (`make catalog`, o "
                          "`fitos catalog fetch`, `build-media` e `import`)"))
    if has_user_data(db):
        if not force:
            raise DemoError(_("La base ya tiene sesiones o exámenes; usa --wipe-existing para BORRARLOS y sembrar "
                              "la demo (nunca en una base real)"))
        wipe(db)
    today = today or local_today()
    start = today - timedelta(weeks=weeks)
    rng = random.Random(seed)
    last_msg = db.scalar(select(func.max(WaMessage.id))) or 0
    last_audit = db.scalar(select(func.max(AuditLog.id))) or 0
    summary = DemoSummary()

    gym = _setup_profile(db, today)
    _seed_training(db, rng, gym, start, today, summary)
    summary.body_metrics = _seed_body(db, rng, start, today)
    _seed_labs(db, today, summary)

    # Nothing from the demo may reach WhatsApp; the per-set audit trail is noise for a demo user.
    db.execute(delete(WaMessage).where(WaMessage.id > last_msg, WaMessage.direction == "out"))
    db.execute(delete(AuditLog).where(AuditLog.id > last_audit))
    db.merge(JobRun(job="morning_plan", period_key=today.isoformat(), ok=True, detail="demo seed: no se envía"))
    db.flush()
    return summary


# ── CLI ─────────────────────────────────────────────────────────────────────


def cmd_demo_seed(args) -> int:
    from .core.db import session_scope

    try:
        with session_scope() as db:
            s = seed_demo(db, weeks=args.weeks, seed=args.seed, force=args.wipe_existing)
    except DemoError as e:
        print(f"demo seed: {e}")
        return 1
    print("demo seed: " + _("{sessions} sesiones ({missed} días perdidos), {sets} sets, {pr_sets} sets con PR, "
                            "{increases} subidas de carga, {metrics} medidas, {reports} informes con {results} "
                            "resultados", sessions=s.sessions, missed=s.missed, sets=s.sets, pr_sets=s.pr_sets,
                            increases=s.weight_increases, metrics=s.body_metrics, reports=s.lab_reports,
                            results=s.lab_results) + (_(", plan de hoy listo") if s.today_plan else ""))
    return 0


def register(sub) -> None:
    demo = sub.add_parser("demo", help="Datos sintéticos de demostración").add_subparsers(dest="demo_cmd",
                                                                                          required=True)
    s = demo.add_parser("seed", help="Llena una base vacía con un usuario de demostración")
    s.add_argument("--weeks", type=int, default=10)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--wipe-existing", action="store_true",
                   help="DESTRUCTIVO: borra sesiones, medidas y exámenes antes de sembrar (solo bases de prueba)")
    s.set_defaults(fn=cmd_demo_seed)
