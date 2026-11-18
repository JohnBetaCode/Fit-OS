"""Everything you logged, in one ZIP: readable CSVs, every table as CSV and as one JSON, and a printable
report (open `report.html` and print it to PDF). Nothing here leaves the server unless you download it."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from .core.models import (
    AuditLog,
    BiomarkerRange,
    BodyMetric,
    ChatMessage,
    ChatThread,
    DayLog,
    EquipmentProfile,
    Exercise,
    LabReport,
    LabResult,
    PlannedItem,
    PlannedSession,
    Profile,
    Program,
    ProgramDay,
    ProgressPhoto,
    Setting,
    WorkoutSession,
    WorkoutSet,
)
from .core.timeutil import local_now

# Your data, table by table (the exercise bank itself is public and rebuildable, so only your favourites go)
TABLES = [Profile, EquipmentProfile, Program, ProgramDay, PlannedSession, PlannedItem, WorkoutSession, WorkoutSet,
          BodyMetric, DayLog, ProgressPhoto, LabReport, LabResult, Setting, ChatThread, ChatMessage, AuditLog]


def _plain(v: Any) -> Any:
    if isinstance(v, datetime | date):
        return v.isoformat()
    return v


def _rows(db: Session, model) -> list[dict]:
    cols = [c.key for c in inspect(model).mapper.column_attrs]
    return [{c: _plain(getattr(r, c)) for c in cols} for r in db.scalars(select(model))]


def _csv(rows: list[dict], fields: list[str] | None = None) -> str:
    buf = io.StringIO()
    fields = fields or (list(rows[0]) if rows else [])
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, dict | list) else v for k, v in r.items()})
    return buf.getvalue()


def workout_log(db: Session) -> list[dict]:
    """One row per set, with the date, the focus and the exercise's name."""
    out = []
    rows = db.execute(select(WorkoutSet, WorkoutSession).join(WorkoutSession, WorkoutSet.session_id == WorkoutSession.id)
                      .order_by(WorkoutSession.date, WorkoutSession.id, WorkoutSet.id)).all()
    for s, ws in rows:
        out.append({
            "date": ws.date.isoformat(), "workout_id": ws.id,
            "focus": ws.planned.focus_label if ws.planned else "", "exercise_id": s.exercise_id,
            "exercise": s.exercise.display_name if s.exercise else s.exercise_id,
            "set": "W" if s.is_warmup else s.set_idx, "reps": s.reps, "weight_kg": s.weight_kg, "rir": s.rir,
            "duration_s": s.duration_s, "e1rm_kg": s.e1rm_kg, "prs": " ".join(s.pr_flags or []),
            "session_rpe": ws.session_rpe,
        })
    return out


def labs_log(db: Session) -> list[dict]:
    out = []
    for r in db.scalars(select(LabResult).order_by(LabResult.collected_on, LabResult.biomarker_key)):
        out.append({"date": r.collected_on.isoformat(), "marker_key": r.biomarker_key, "raw_name": r.raw_name,
                    "value": r.value_num if r.value_num is not None else r.value_text, "comparator": r.comparator,
                    "unit": r.unit, "value_canonical": r.value_canonical, "ref_low": r.ref_low, "ref_high": r.ref_high,
                    "flag": r.flag, "needs_review": r.needs_review, "lab": r.report.lab_name if r.report else None})
    return out


def report_context(db: Session) -> dict:
    from .body import service as body
    from .training import metrics
    from .training.weekly import week_report, week_start

    prof = body.get_profile(db)
    today = local_now().date()
    lifts = {}
    for s in db.scalars(select(WorkoutSet).where(WorkoutSet.e1rm_kg.is_not(None), WorkoutSet.is_warmup.is_(False))):
        lifts.setdefault(s.exercise_id, s.exercise.display_name if s.exercise else s.exercise_id)
    lift_rows = []
    for ex_id, name in lifts.items():
        series = metrics.e1rm_series(db, ex_id)
        if series:
            lift_rows.append({"name": name, "first": series[0], "best": max(series, key=lambda x: x[1]),
                              "last": series[-1], "n": len(series)})
    lift_rows.sort(key=lambda r: -r["n"])
    try:
        from .labs import service as labs

        lab_view = labs.overview(db, prof.sex, prof.birth_date)
    except Exception:  # noqa: BLE001 — a report without labs is still a report
        lab_view = None
    weeks = [week_report(db, week_start(today) - timedelta(days=7 * i)) for i in range(8)]
    return {
        "profile": prof, "age": body.age(prof), "goal": body.goal_label(prof.primary_goal),
        "experience": body.experience_label(prof.experience), "generated": local_now(),
        "weight": body.metric_series(db, "weight_kg"), "lifts": lift_rows[:15], "labs": lab_view,
        "weeks": weeks, "consistency": metrics.consistency(db, 12),
        "workouts": len(db.scalars(select(WorkoutSession.id)).all()),
        "sets": len(db.scalars(select(WorkoutSet.id).where(WorkoutSet.is_warmup.is_(False))).all()),
        "prs": metrics.recent_prs(db, 15),
    }


def render_report(db: Session) -> str:
    from .web.ui import templates

    return templates.env.get_template("export_report.html").render(**report_context(db))


def build_zip(db: Session) -> bytes:
    stamp = local_now().strftime("%Y-%m-%d")
    root = f"fit-os-export-{stamp}"
    buf = io.BytesIO()
    every: dict[str, list[dict]] = {}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{root}/README.txt", README.format(date=stamp))
        z.writestr(f"{root}/workouts_sets.csv", _csv(workout_log(db)))
        z.writestr(f"{root}/body_metrics.csv", _csv(_rows(db, BodyMetric)))
        z.writestr(f"{root}/labs.csv", _csv(labs_log(db)))
        favs = [{"exercise_id": e.id, "name": e.display_name} for e in
                db.scalars(select(Exercise).where(Exercise.favorite.is_(True)))]
        z.writestr(f"{root}/favorite_exercises.csv", _csv(favs, ["exercise_id", "name"]))
        targets = [r for r in _rows(db, BiomarkerRange) if r.get("kind") == "user_target"]
        z.writestr(f"{root}/lab_targets.csv", _csv(targets))
        for model in TABLES:
            rows = _rows(db, model)
            every[model.__tablename__] = rows
            z.writestr(f"{root}/tables/{model.__tablename__}.csv", _csv(rows))
        every["favorite_exercises"] = favs
        every["lab_targets"] = targets
        z.writestr(f"{root}/fit-os.json", json.dumps({"exported_at": local_now().isoformat(), "tables": every},
                                                      ensure_ascii=False, indent=1, default=str))
        z.writestr(f"{root}/report.html", render_report(db))
        from .body import photos

        for ph in db.scalars(select(ProgressPhoto).order_by(ProgressPhoto.taken_on)):
            f = photos.file_of(ph)
            if f:
                z.write(f, f"{root}/photos/{f.name}")
    return buf.getvalue()


README = """Fit-OS export · {date}

workouts_sets.csv       one row per set: date, focus, exercise, reps, kg, RIR, e1RM, PRs (W = warm-up)
body_metrics.csv        body weight and other measurements
labs.csv                every lab result with its unit and the lab's reference range
lab_targets.csv         your own targets per marker
favorite_exercises.csv  exercises you starred
tables/                 every table as it is in the database (CSV)
fit-os.json             the same tables in one JSON file
report.html             a printable summary: open it in a browser and print to PDF
photos/                 your progress photos (named by date)

This file contains health data. Keep it somewhere private.
"""
