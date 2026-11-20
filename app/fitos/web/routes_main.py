"""Today, Workouts, Progress, Profile, Settings, Integrations and Coach tabs."""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..body import photos
from ..body import service as body
from ..catalog.names import title_en
from ..core import prefs
from ..core.db import get_db
from ..core.models import (
    ChatMessage,
    ChatThread,
    EquipmentProfile,
    Exercise,
    PlannedSession,
    ProgressPhoto,
    WorkoutSession,
    WorkoutSet,
)
from ..core.settings import get_settings
from ..core.timeutil import day_letters, fmt_date, local_today, weekday_short
from ..i18n import LANGS, _, pick
from ..training import service as tr
from ..wa import cards
from ..wa.outbox import gym_chat
from .ui import form_int, form_num, render

log = logging.getLogger("fitos.web")
router = APIRouter()


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _labs_alerts(db: Session) -> dict | None:
    try:
        from ..labs import service as labs

        p = body.get_profile(db)
        ov = labs.overview(db, p.sex, p.birth_date)
        return ov.get("counts") if isinstance(ov, dict) else None
    except Exception:  # noqa: BLE001 — los exámenes no deben tumbar la portada
        log.debug("sin datos de exámenes", exc_info=True)
        return None


def _muscle_bars(db: Session, today: date) -> list[dict]:
    """Hard sets per muscle in the last 7 days (a window that moves daily: no Monday reset)."""
    from ..catalog.service import muscle_label
    from ..training import metrics as m

    lo, hi = int(prefs.get(db, "weekly_sets_target_min")), int(prefs.get(db, "weekly_sets_target_max"))
    data = m.recent_hard_sets_by_muscle(db, today)
    rows = []
    for key in cards.MUSCLE_ES:
        v = round(float(data.get(key, 0.0)), 1)
        status = "low" if v < lo else ("high" if v > hi else "ok")
        rows.append({"key": key, "label": muscle_label(key), "value": v, "pct": min(100, v / hi * 100),
                     "status": status})
    rows.sort(key=lambda r: -r["value"])
    return rows


def _timeline(db: Session, today: date) -> dict:
    """Today as a timeline: plans already written for the coming days, and the last finished workouts."""
    from ..training.metrics import session_summary

    upcoming = db.scalars(select(PlannedSession).where(PlannedSession.date > today,
                                                       PlannedSession.status.in_(("proposed", "accepted")))
                          .order_by(PlannedSession.date, PlannedSession.id.desc()).limit(7)).all()
    seen, coming = set(), []
    for p in upcoming:  # one per day (the newest)
        if p.date not in seen:
            seen.add(p.date)
            coming.append(p)
    past = db.scalars(select(WorkoutSession).where(WorkoutSession.date < today, WorkoutSession.state == "completed")
                      .order_by(WorkoutSession.date.desc(), WorkoutSession.id.desc()).limit(10)).all()
    return {"upcoming": coming, "past": [(s, session_summary(db, s.id)) for s in past]}


def _recovery_card(db: Session) -> dict | None:
    """The small body map on Today; None when nothing was trained in the last weeks."""
    from .routes_body import recovery_view

    view = recovery_view(db)
    return view if any(i["trained"] for i in view["items"]) else None


def _rec_labels() -> dict[str, str]:
    from .routes_body import status_labels

    return status_labels()


# ── Hoy ─────────────────────────────────────────────────────────────────────


@router.get("/", response_class=HTMLResponse)
def today_page(request: Request, db: Session = Depends(get_db)):
    from ..training import metrics as m

    if not body.ensure_defaults(db).onboarded and not getattr(request.state, "read_only", False):
        return RedirectResponse("/bienvenida", status_code=303)  # a new person: the welcome flow first
    today = local_today()
    plan = tr.plan_for(db, today)
    ws = tr.active_session(db)
    prof = body.get_profile(db)
    ws_start = _week_start(today)
    week_sessions = {s.date for s in db.scalars(select(WorkoutSession).where(WorkoutSession.date >= ws_start))}
    letters = day_letters()
    days = [{"label": letters[i], "date": ws_start + timedelta(days=i),
             "done": (ws_start + timedelta(days=i)) in week_sessions,
             "planned": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][i] in (prof.schedule or {})} for i in range(7)]
    bars = _muscle_bars(db, today)
    total_sets = sum(b["value"] for b in bars)
    target_sets = int(prefs.get(db, "weekly_sets_target_min")) * 8
    place = None
    if plan and plan.equipment_profile_id:
        ep = db.get(EquipmentProfile, plan.equipment_profile_id)
        place = body.equipment_name(ep) if ep else None
    return render(
        request, "today.html", "hoy", today=today, today_label=fmt_date(today), plan=plan, ws=ws, place=place,
        days=days, bars=bars[:8], load_pct=min(100, round(total_sets / max(target_sets, 1) * 100)),
        consistency=f"{len(week_sessions)}/{prof.days_per_week}", prs=m.recent_prs(db, 5),
        labs=_labs_alerts(db), profiles=body.equipment_profiles(db), w=cards.weight_text,
        rec=_recovery_card(db), rec_labels=_rec_labels(), timeline=_timeline(db, today),
        rep_range=cards.rep_range, onboarded=prof.onboarded, has_wa=bool(gym_chat()), has_catalog=db.scalar(
            select(Exercise.id).where(Exercise.enabled.is_(True)).limit(1)) is not None,
    )


@router.post("/hoy/plan")
def today_plan(request: Request, action: str = Form("build"), equipment_profile_id: str = Form(""),
               db: Session = Depends(get_db)):
    today = local_today()
    try:
        equipment_profile_id = form_int(equipment_profile_id)
        if action == "skip":
            tr.skip_today(db, today)
        elif action == "build":
            tr.get_or_build_plan(db, today, equipment_profile_id, replace=equipment_profile_id is not None)
        elif action == "next_focus":
            prog = tr.active_program(db)
            if prog and prog.days:
                prog.rotation_idx = (prog.rotation_idx + 1) % len(prog.days)
            plan = tr.plan_for(db, today)
            tr.get_or_build_plan(db, today, plan.equipment_profile_id if plan else None, replace=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, _("No pude armar el plan: {error}", error=e)) from e
    return RedirectResponse("/", status_code=303)


# ── Entrenos ────────────────────────────────────────────────────────────────


@router.get("/entrenos", response_class=HTMLResponse)
def sessions_page(request: Request, deleted: int = 0, db: Session = Depends(get_db)):
    from ..training.metrics import session_summary

    rows = [(s, session_summary(db, s.id)) for s in tr.recent_sessions(db, 60)]
    return render(request, "sessions.html", "entrenos", rows=rows, fmt=fmt_date, wday=weekday_short,
                  deleted=deleted)


@router.get("/entrenos/{sid}", response_class=HTMLResponse)
def session_detail(sid: int, request: Request, saved: int = 0, db: Session = Depends(get_db)):
    from ..training.metrics import session_summary

    ws = db.get(WorkoutSession, sid)
    if not ws:
        raise HTTPException(404)
    groups: dict[str, dict] = {}
    for st in ws.sets:
        g = groups.setdefault(st.exercise_id, {"ex": st.exercise, "sets": [], "item": None})
        g["sets"].append(st)
    if ws.planned:
        for it in ws.planned.items:
            g = groups.setdefault(it.exercise_id, {"ex": it.exercise, "sets": [], "item": it})
            g["item"] = it
    return render(request, "session_detail.html", "entrenos", ws=ws, groups=list(groups.values()),
                  summary=session_summary(db, ws.id), fmt=fmt_date, describe=cards.describe_set,
                  rep_range=cards.rep_range, saved=saved, today=local_today())


@router.post("/entrenos/sets/{set_id}")
def edit_set(set_id: int, reps: int = Form(...), weight_kg: str = Form(""), rir: str = Form(""),
             delete: str | None = Form(None), db: Session = Depends(get_db)):
    st = db.get(WorkoutSet, set_id)
    if not st:
        raise HTTPException(404)
    sid = st.session_id
    try:
        if delete:
            tr.delete_set(db, st)
        else:
            tr.update_set(db, st, reps, form_num(weight_kg), form_num(rir))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return RedirectResponse(f"/entrenos/{sid}", status_code=303)


@router.post("/entrenos/{sid}/sets")
def add_set(sid: int, exercise_id: str = Form(...), reps: int = Form(...), weight_kg: str = Form(""),
            rir: str = Form(""), db: Session = Depends(get_db)):
    ws = db.get(WorkoutSession, sid)
    if not ws:
        raise HTTPException(404)
    try:
        tr.add_set(db, ws, exercise_id, reps, form_num(weight_kg), form_num(rir))
    except LookupError as e:
        raise HTTPException(404) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return RedirectResponse(f"/entrenos/{sid}", status_code=303)


@router.post("/entrenos/{sid}/editar")
def edit_session(sid: int, on: str = Form(..., alias="date"), rpe: str = Form(""),
                 duration_min: str = Form(""), notes: str = Form(""), db: Session = Depends(get_db)):
    ws = db.get(WorkoutSession, sid)
    if not ws:
        raise HTTPException(404)
    try:
        tr.update_session(db, ws, on=date.fromisoformat(on), rpe=form_int(rpe), duration_min=form_int(duration_min),
                          notes=notes)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return RedirectResponse(f"/entrenos/{sid}?saved=1", status_code=303)


@router.post("/entrenos/{sid}/reabrir")
def reopen_session(sid: int, db: Session = Depends(get_db)):
    """Back to in progress, in Train, to add or fix exercises and sets."""
    ws = db.get(WorkoutSession, sid)
    if not ws:
        raise HTTPException(404)
    try:
        tr.reopen_session(db, ws)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return RedirectResponse("/entreno", status_code=303)


@router.post("/entrenos/{sid}/borrar")
def delete_session(sid: int, db: Session = Depends(get_db)):
    ws = db.get(WorkoutSession, sid)
    if not ws:
        raise HTTPException(404)
    tr.delete_session(db, ws)
    return RedirectResponse("/entrenos?deleted=1", status_code=303)


# ── Progreso ────────────────────────────────────────────────────────────────


@router.get("/progreso", response_class=HTMLResponse)
def progress_page(request: Request, exercise_id: str | None = None, db: Session = Depends(get_db)):
    from ..training import metrics as m

    prof = body.get_profile(db)
    today = local_today()
    bars = _muscle_bars(db, today)
    cons = m.consistency(db, weeks=12, target_per_week=prof.days_per_week)
    trained = db.execute(
        select(WorkoutSet.exercise_id, Exercise.name_es, Exercise.name_en, func.count(WorkoutSet.id)).join(Exercise)
        .where(WorkoutSet.e1rm_kg.is_not(None))
        .group_by(WorkoutSet.exercise_id, Exercise.name_es, Exercise.name_en)
    ).all()
    options = sorted(((r[0], pick(r[1], title_en(r[2]))) for r in trained), key=lambda x: x[1])
    # Por defecto, el ejercicio con más historial (la curva más útil), no el primero alfabético.
    most = max(trained, key=lambda r: r[3])[0] if trained else None
    sel = exercise_id or most
    series = [(d.isoformat(), round(v, 1)) for d, v in m.e1rm_series(db, sel)] if sel else []
    weights = [(b.measured_on.isoformat(), b.value) for b in body.metric_series(db, "weight_kg", 365)]
    total_sets = sum(b["value"] for b in bars)
    lo = int(prefs.get(db, "weekly_sets_target_min"))
    this_week = cons.get("weeks", [{}])[-1].get("count", 0) if isinstance(cons, dict) and cons.get("weeks") else 0
    return render(
        request, "progress.html", "progreso", bars=bars, cons=cons, options=options, sel=sel,
        series_json=json.dumps(series), weights_json=json.dumps(weights), prs=m.recent_prs(db, 15),
        rings=[
            {"label": _("Carga de la semana"), "pct": min(100, round(total_sets / max(lo * 8, 1) * 100)),
             "text": _("{n} sets duros", n=f"{total_sets:g}"), "note": _("meta ≥ {n}", n=lo * 8)},
            {"label": _("Constancia"), "pct": min(100, round(this_week / max(prof.days_per_week, 1) * 100)),
             "text": _("{done}/{target} sesiones", done=this_week, target=prof.days_per_week),
             "note": _("esta semana")},
            {"label": _("Preparación"), "pct": None, "text": _("Fase 2"), "note": _("check-in matutino o wearable")},
        ],
        lo=lo, hi=int(prefs.get(db, "weekly_sets_target_max")),
        measures=body.latest_by_kind(db, body.MEASUREMENTS), measure_kinds=body.MEASUREMENTS,
        metric_label=body.metric_label, metric_units=body.METRIC_UNITS, photo_days=photos.by_date(db),
        today=local_today(),
    )


PERIODS = {"4s": 28, "12s": 84, "6m": 182, "todo": None}
SORTS = {
    "reciente": lambda r: r["last_date"], "nombre": lambda r: r["name"].lower(), "sesiones": lambda r: r["sessions"],
    "cambio": lambda r: r["change_pct"] if r["change_pct"] is not None else -1e9,
    "mejor": lambda r: r["best"] or 0, "musculo": lambda r: (r["muscle"] or "", r["name"].lower()),
}


def _spark(series: list, w: int = 96, h: int = 24) -> dict:
    """Polyline points for an inline SVG sparkline (2 px inset so the stroke isn't clipped)."""
    vals = [v for _d, v in series]
    if not vals:
        return {"points": "", "last": None}
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    n = len(vals)
    pts = [(2 + (i / (n - 1) * (w - 4) if n > 1 else (w - 4) / 2), 2 + (h - 4) * (1 - (v - lo) / span)) for i, v in
           enumerate(vals)]
    return {"points": " ".join(f"{x:.1f},{y:.1f}" for x, y in pts), "last": pts[-1]}


@router.get("/progreso/ejercicios", response_class=HTMLResponse)
def my_exercises(request: Request, periodo: str = "todo", musculo: str = "", q: str = "", orden: str = "reciente",
                 dir: str = "", db: Session = Depends(get_db)):
    from ..catalog.service import muscle_label
    from ..training import metrics as m

    days = PERIODS.get(periodo)
    since = local_today() - timedelta(days=days) if days else None
    rows = m.exercise_table(db, since)
    muscles = sorted({r["muscle"] for r in rows if r["muscle"]}, key=lambda k: muscle_label(k))
    if musculo:
        rows = [r for r in rows if r["muscle"] == musculo]
    if q.strip():
        words = q.lower().split()
        rows = [r for r in rows if all(w in r["name"].lower() for w in words)]
    orden = orden if orden in SORTS else "reciente"
    desc = (dir or ("asc" if orden in ("nombre", "musculo") else "desc")) == "desc"
    rows.sort(key=SORTS[orden], reverse=desc)
    for r in rows:
        r["spark"] = _spark(r["series"])
    stats = {"total": len(rows), "up": sum(1 for r in rows if (r["change_pct"] or 0) > 0),
             "down": sum(1 for r in rows if (r["change_pct"] or 0) < 0)}
    return render(request, "progress_exercises.html", "progreso", rows=rows, muscles=muscles, periodo=periodo,
                  musculo=musculo, q=q, orden=orden, desc=desc, stats=stats, periods=list(PERIODS),
                  w=cards.weight_text, describe=cards.describe_set)


@router.post("/progreso/medidas")
async def log_measures(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    on = date.fromisoformat(form["date"]) if form.get("date") else local_today()
    if on > local_today():
        raise HTTPException(400, _("La fecha no puede ser futura"))
    for kind in body.MEASUREMENTS:
        raw = (form.get(kind) or "").strip().replace(",", ".")
        if raw:
            try:
                body.log_metric(db, kind, float(raw), on)
            except ValueError as e:
                raise HTTPException(400, str(e)) from e
    return RedirectResponse("/progreso#medidas", status_code=303)


@router.post("/progreso/fotos")
async def upload_photos(files: list[UploadFile] = File(...), taken_on: str = Form(""), note: str = Form(""),
                        db: Session = Depends(get_db)):
    on = date.fromisoformat(taken_on) if taken_on else local_today()
    if on > local_today():
        raise HTTPException(400, _("La fecha no puede ser futura"))
    for f in files:
        data = await f.read(photos.MAX_BYTES + 1)
        try:
            photos.save(db, data, f.content_type or "", taken_on=on, note=note)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    return RedirectResponse("/progreso#fotos", status_code=303)


@router.get("/fotos/{photo_id}")
def photo_file(photo_id: int, db: Session = Depends(get_db)):
    from fastapi.responses import FileResponse

    ph = db.get(ProgressPhoto, photo_id)
    f = photos.file_of(ph) if ph else None
    if f is None:
        raise HTTPException(404)
    return FileResponse(f, media_type=ph.mimetype, headers={"Cache-Control": "private, max-age=86400"})


@router.post("/progreso/fotos/{photo_id}/borrar")
def delete_photo(photo_id: int, db: Session = Depends(get_db)):
    ph = db.get(ProgressPhoto, photo_id)
    if ph is None:
        raise HTTPException(404)
    photos.delete(db, ph)
    return RedirectResponse("/progreso#fotos", status_code=303)


@router.post("/progreso/peso")
def log_weight(weight_kg: float = Form(...), db: Session = Depends(get_db)):
    try:
        body.log_metric(db, "weight_kg", weight_kg)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return RedirectResponse("/progreso", status_code=303)


# ── Perfil ──────────────────────────────────────────────────────────────────


@router.get("/perfil", response_class=HTMLResponse)
def profile_page(request: Request, saved: int = 0, db: Session = Depends(get_db)):
    prof = body.ensure_defaults(db)
    disliked = [db.get(Exercise, i) for i in prof.disliked_exercise_ids or []]
    return render(
        request, "profile.html", "perfil", p=prof, age=body.age(prof), weight=body.latest_metric(db),
        eps=body.equipment_profiles(db), goals=body.labels("goal"), levels=body.labels("experience"),
        weekdays=body.WEEKDAYS, weekday_names=body.labels("weekday"), joints=body.labels("joint"),
        tag_es=body.labels("tag"), sexes=body.labels("sex"), equipment_tags=body.EQUIPMENT_TAGS,
        require_tags=body.REQUIRE_TAGS, disliked=[d for d in disliked if d], saved=saved,
    )


@router.post("/perfil")
async def profile_save(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    prof = body.ensure_defaults(db)
    prof.display_name = (form.get("display_name") or "").strip() or None
    prof.sex = form.get("sex") or prof.sex
    bd = form.get("birth_date")
    prof.birth_date = date.fromisoformat(bd) if bd else prof.birth_date
    prof.height_cm = float(form["height_cm"]) if form.get("height_cm") else prof.height_cm
    prof.experience = form.get("experience") or prof.experience
    prof.primary_goal = form.get("primary_goal") or prof.primary_goal
    prof.goal_notes = (form.get("goal_notes") or "").strip() or None
    prof.coach_notes = (form.get("coach_notes") or "").strip() or None
    prof.session_minutes = int(form.get("session_minutes") or prof.session_minutes)
    schedule = {}
    for d in body.WEEKDAYS:
        if form.get(f"day_{d}"):
            schedule[d] = {"equipment_profile_id": int(form.get(f"ep_{d}") or 0) or None,
                           "time": form.get(f"time_{d}") or "06:30"}
    prof.schedule = schedule
    new_days = max(1, len(schedule))
    changed_days = new_days != prof.days_per_week
    prof.days_per_week = new_days
    injuries = []
    for j in body.JOINTS_ES:
        if form.get(f"inj_{j}"):
            injuries.append({"area": j, "severity": form.get(f"sev_{j}") or "leve", "active": True,
                             "notes": (form.get(f"injnote_{j}") or "").strip()})
    prof.injuries = injuries
    if form.get("weight_kg"):
        with contextlib.suppress(ValueError):
            body.log_metric(db, "weight_kg", float(form["weight_kg"]))
    prof.onboarded = True
    if changed_days or form.get("reset_program"):
        prog = tr.active_program(db)
        if prog:
            prog.status = "archived"
            db.flush()
        tr.ensure_program(db)
    return RedirectResponse("/perfil?saved=1", status_code=303)


@router.post("/perfil/equipo/{ep_id}")
async def equipment_save(ep_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    ep = db.get(EquipmentProfile, ep_id)
    if not ep:
        raise HTTPException(404)
    ep.name = (form.get("name") or ep.name).strip()
    ep.tags = [t for t in body.ALL_TAGS if form.get(f"tag_{t}")]
    loads = dict(ep.loads or {})
    dbs = form.get("dumbbell_kg") or ""
    try:
        loads["dumbbell_kg"] = sorted({float(x.replace(",", ".")) for x in dbs.replace(";", " ").split() if x.strip()})
    except ValueError as e:
        raise HTTPException(400, _("Pesos de mancuernas inválidos")) from e
    for k in ("barbell_step_kg", "machine_step_kg", "cable_step_kg"):
        if form.get(k):
            loads[k] = float(form[k])
    ep.loads = loads
    if form.get("is_default"):
        for other in body.equipment_profiles(db):
            other.is_default = other.id == ep.id
    return RedirectResponse("/perfil?saved=1#equipo", status_code=303)


@router.post("/perfil/equipo")
def equipment_new(name: str = Form(...), db: Session = Depends(get_db)):
    db.add(EquipmentProfile(name=name.strip()[:48], kind="gym", tags=["bodyweight"], loads={}))
    return RedirectResponse("/perfil#equipo", status_code=303)


# ── Ajustes ─────────────────────────────────────────────────────────────────


@router.get("/ajustes", response_class=HTMLResponse)
async def settings_page(request: Request, saved: int = 0, db: Session = Depends(get_db)):
    from ..coach.llm import spent_today_usd
    from ..core.models import JobRun, WaMessage
    from ..jobs import waha_health
    from ..wa.webhook import session_status, stats
    from .planning import program_view

    s = get_settings()
    last_in = db.scalar(select(WaMessage).where(WaMessage.direction == "in").order_by(WaMessage.id.desc()).limit(1))
    queued = db.scalar(select(WaMessage.id).where(WaMessage.status == "queued").limit(1))
    failed = db.scalars(select(WaMessage).where(WaMessage.status == "failed").order_by(WaMessage.id.desc()).limit(5)).all()
    jobs = db.scalars(select(JobRun).order_by(JobRun.ran_at.desc()).limit(12)).all()
    return render(
        request, "settings.html", "ajustes", p=prefs.all_prefs(db), saved=saved, program=program_view(db),
        health={"waha": waha_health, "session_event": session_status, "stats": dict(stats), "last_in": last_in,
                "queued": queued is not None, "failed": failed, "jobs": jobs, "llm_mode": s.llm_mode,
                "llm_spent": spent_today_usd(), "gym_chat": bool(gym_chat()), "sha": s.git_sha[:7]},
        cert_ok=(s.data_dir / "caddy" / "root.crt").is_file(), portal=s.portal_base_url,
    )


@router.post("/ajustes")
async def settings_save(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    for key, cast in [("morning_hour", str), ("quiet_start", str), ("quiet_end", str), ("clip_policy", str),
                      ("wa_cards", str), ("wa_mode", str), ("weekly_summary_hour", str), ("rest_seconds_compound", int), ("rest_seconds_isolation", int),
                      ("llm_daily_budget_usd", float), ("weekly_sets_target_min", int),
                      ("weekly_sets_target_max", int), ("day_close_hour", str)]:
        if form.get(key) not in (None, ""):
            prefs.set_(db, key, cast(form[key]))
    for flag in ("morning_enabled", "rest_alert", "warmup_sets", "weekly_summary_enabled"):
        prefs.set_(db, flag, bool(form.get(flag)))
    if form.get("language") in LANGS:
        prefs.set_(db, "language", form["language"])  # also refreshes the cached language: the redirect is translated
    return RedirectResponse("/ajustes?saved=1", status_code=303)


@router.post("/ajustes/programa")
async def program_save(request: Request, db: Session = Depends(get_db)):
    from .planning import apply_program_form

    form = await request.form()
    apply_program_form(db, dict(form))
    return RedirectResponse("/ajustes?saved=1#programa", status_code=303)


@router.get("/ajustes/exportar")
def export_zip(db: Session = Depends(get_db)):
    from fastapi.responses import Response

    from ..core.timeutil import local_now
    from ..export import build_zip

    name = f"fit-os-export-{local_now().strftime('%Y-%m-%d')}.zip"
    return Response(build_zip(db), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.get("/ajustes/certificado")
def caddy_root_cert():
    """Caddy's internal CA root (copied by ops/server.sh on deploy) so a phone can trust the dashboard."""
    from fastapi.responses import FileResponse

    f = get_settings().data_dir / "caddy" / "root.crt"
    if not f.is_file():
        raise HTTPException(404, _("Todavía no hay certificado: se copia al desplegar con el perfil edge."))
    return FileResponse(f, media_type="application/x-x509-ca-cert", filename="fit-os-ca.crt")


@router.get("/ajustes/reporte", response_class=HTMLResponse)
def export_report(db: Session = Depends(get_db)):
    from ..export import render_report

    return HTMLResponse(render_report(db), headers={"Cache-Control": "no-store"})


# ── Integraciones ───────────────────────────────────────────────────────────


@router.get("/integraciones", response_class=HTMLResponse)
async def integrations_page(request: Request, db: Session = Depends(get_db)):
    from ..coach.llm import spent_today_usd
    from ..jobs import waha_health

    s = get_settings()
    return render(request, "integrations.html", "integraciones", waha=waha_health, gym_chat=bool(gym_chat()),
                  llm_mode=s.llm_mode, llm_spent=spent_today_usd(),
                  budget=float(prefs.get(db, "llm_daily_budget_usd")))


# ── Coach ───────────────────────────────────────────────────────────────────


@router.get("/coach", response_class=HTMLResponse)
def coach_page(request: Request, t: int | None = None, off: int = 0, db: Session = Depends(get_db)):
    from ..coach.llm import available

    threads = db.scalars(select(ChatThread).order_by(ChatThread.id.desc()).limit(20)).all()
    thread = db.get(ChatThread, t) if t else None
    msgs = []
    if thread:
        msgs = [m for m in db.scalars(select(ChatMessage).where(ChatMessage.thread_id == thread.id)
                                       .order_by(ChatMessage.seq)) if m.visible_text and m.role in ("user", "assistant")]
    return render(request, "coach.html", "coach", threads=threads, thread=thread, msgs=msgs, llm_ok=available(),
                  off=off)


@router.post("/coach", response_class=HTMLResponse)
async def coach_send(request: Request, text: str = Form(...), thread_id_raw: str = Form("", alias="thread_id")):
    from ..coach.agent import coach_reply
    from ..core.db import session_scope

    try:
        thread_id = form_int(thread_id_raw)
    except ValueError:
        thread_id = None
    text = text.strip()[:2000]
    if not text:
        return RedirectResponse("/coach", status_code=303)
    with session_scope() as db:
        if thread_id is None:
            t = ChatThread(channel="web", day=local_today(), title=text[:60])
            db.add(t)
            db.flush()
            thread_id = t.id
    reply = await coach_reply(text, channel="web", thread_id=thread_id)
    return RedirectResponse(f"/coach?t={thread_id}" + ("&off=1" if reply is None else ""), status_code=303)
