"""Train (`/entreno`): the assisted workout on the web, for people without WhatsApp or without signal.

Three screens on one URL, picked by state: set up the day (place, focus, how many, missing equipment, random or
suggested, or choose them yourself) → review the proposal (swap, another at random, add, remove, reorder, edit
targets) → live (log sets, rest timer, next, swap on the spot, finish). It uses the same plan and session as
WhatsApp, so both see the same workout. Logic lives in `training/assist.py` and `training/service.py`.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..body import service as body
from ..catalog import service as catalog
from ..core import prefs
from ..core.db import get_db
from ..core.models import EquipmentProfile, PlannedItem, PlannedSession, WorkoutSession, WorkoutSet
from ..core.timeutil import local_today
from ..i18n import _
from ..training import assist
from ..training import service as tr
from ..training import week as wk
from ..wa import cards
from ..wa.outbox import gym_chat
from ..wa.parser import SetEntry
from .ui import form_int, form_num, render

router = APIRouter()


def _fail(e: Exception) -> HTTPException:
    return HTTPException(400, str(e))


def _item(db: Session, item_id: int) -> PlannedItem:
    it = db.get(PlannedItem, item_id)
    if it is None:
        raise HTTPException(404)
    return it


def _plan(db: Session, plan_id: int) -> PlannedSession:
    p = db.get(PlannedSession, plan_id)
    if p is None:
        raise HTTPException(404)
    return p


def _back(plan: PlannedSession | None = None, **q) -> RedirectResponse:
    params = {**({"plan": plan.id} if plan is not None and plan.status in wk.EDITABLE else {}), **q}
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    return RedirectResponse("/entreno" + (f"?{qs}" if qs else ""), status_code=303)


def _last_time(db: Session, ex_id: str) -> str:
    """Your sets of that exercise in the last finished workout (never the one in progress)."""
    sid = db.scalar(select(WorkoutSession.id).join(WorkoutSet, WorkoutSet.session_id == WorkoutSession.id)
                    .where(WorkoutSet.exercise_id == ex_id, WorkoutSession.state == "completed")
                    .order_by(WorkoutSession.date.desc(), WorkoutSession.id.desc()).limit(1))
    if sid is None:
        return ""
    ws = db.get(WorkoutSession, sid)
    sets = [st for st in ws.sets if st.exercise_id == ex_id and not st.is_warmup and st.reps]
    txt = " · ".join(f"{st.reps}×{st.weight_kg:g}" if st.weight_kg else f"{st.reps}" for st in sets[:6])
    return _("Última vez ({date}): {sets}", date=ws.date.strftime("%d/%m"), sets=txt) if txt else ""


def _previous_sets(db: Session, ex_id: str) -> list[WorkoutSet]:
    """The sets of that exercise in the last finished workout ("previous" column)."""
    sid = db.scalar(select(WorkoutSession.id).join(WorkoutSet, WorkoutSet.session_id == WorkoutSession.id)
                    .where(WorkoutSet.exercise_id == ex_id, WorkoutSession.state == "completed")
                    .order_by(WorkoutSession.date.desc(), WorkoutSession.id.desc()).limit(1))
    if sid is None:
        return []
    return [st for st in db.get(WorkoutSession, sid).sets if st.exercise_id == ex_id and not st.is_warmup]


def _rest_seconds(db: Session, item: PlannedItem) -> int:
    key = "rest_seconds_compound" if item.exercise.mechanic == "compound" else "rest_seconds_isolation"
    return int(prefs.get(db, key) or 90)


@router.get("/entreno", response_class=HTMLResponse)
def train(request: Request, plan: int | None = None, nuevo: int = 0, descanso: int = 0, error: str = "",
          grupos: str = "", db: Session = Depends(get_db)):
    body.ensure_defaults(db)
    today = local_today()
    ws = tr.active_session(db)
    common = {"groups": assist.group_labels(), "missing_labels": assist.missing_labels(),
              "prefer_labels": assist.prefer_labels(), "w": cards.weight_text,
              "rep_range": cards.rep_range, "error": error}
    if ws is not None and ws.planned is not None and not nuevo:
        current = tr.current_item(db, ws)
        rows = []
        for it in ws.planned.items:
            done = tr.item_sets(ws, it)
            if it.status == "skipped" and not done:  # dropped: out of sight
                continue
            timed = bool((it.slot or {}).get("duration_min"))
            prev = _previous_sets(db, it.exercise_id)
            left = 0 if it.status in ("skipped", "done") else max(0, (1 if timed else it.sets) - len(done))
            rows.append({"it": it, "sets": done, "current": current is not None and it.id == current.id,
                         "timed": timed, "prev": prev, "left": left,
                         "prefill": done[-1] if done else (prev[0] if prev else None)})
        # in progress (sets, still open) first, then what's left untouched, finished ones at the end
        rows.sort(key=lambda r: (r["it"].status in ("done", "skipped"), not r["sets"]))
        return render(request, "train.html", "entrenar", view="live", ws=ws, plan=ws.planned, rows=rows,
                      rest=descanso, note=assist.parse_note(ws.notes), **common)
    p = _plan(db, plan) if plan else tr.plan_for(db, today)
    if p is not None and p.status in wk.EDITABLE and not nuevo:
        return render(request, "train.html", "entrenar", view="review", plan=p,
                      rows=[{"it": it, "last": _last_time(db, it.exercise_id)} for it in p.items],
                      missing=assist.today_missing(db, p.date), **common)
    setup = assist.last_setup(db, today)
    picked = [g for g in grupos.split(",") if g in assist.GROUPS]
    if picked:  # from the body map: "ready to train"
        setup.focus, setup.groups = "groups", picked
    from ..training import recovery

    ready = recovery.ready_groups(recovery.recovery(db), assist.GROUPS)
    prog = tr.active_program(db)
    next_day = prog.days[prog.rotation_idx % len(prog.days)].name if prog and prog.days else None
    done_today = None
    if p is not None and p.status == "completed":  # today's workout is closed: offer to reopen it
        done_today = db.scalar(select(WorkoutSession.id).where(WorkoutSession.planned_session_id == p.id)
                               .order_by(WorkoutSession.id.desc()).limit(1)) or True
    return render(request, "train.html", "entrenar", view="setup", setup=setup, today=today, next_day=next_day,
                  eps=body.equipment_profiles(db), done_today=done_today, ready_groups=ready, **common)


@router.post("/entreno/armar")
async def train_build(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    try:
        on = date.fromisoformat(form.get("date") or "") if form.get("date") else local_today()
    except ValueError:
        on = local_today()
    setup = assist.Setup(
        on=on, equipment_profile_id=int(form.get("equipment_profile_id") or 0) or None,
        focus="groups" if form.get("focus") == "groups" else "program",
        groups=[g for g in assist.GROUPS if form.get(f"g_{g}")],
        count=int(form.get("count") or 6), missing=[m for m in assist.MISSING_ES if form.get(f"m_{m}")],
        prefer=[t for t in assist.PREFER_ES if form.get(f"p_{t}")],
        mode=form.get("mode") if form.get("mode") in ("smart", "random", "manual") else "smart",
    )
    try:
        if setup.mode == "manual":
            plan = assist.empty_plan(db, setup)
        else:
            plan = assist.build(db, setup)
    except (ValueError, LookupError, RuntimeError) as e:
        return RedirectResponse(f"/entreno?nuevo=1&error={_err(e)}", status_code=303)
    return _back(plan)


def _err(e: Exception) -> str:
    from urllib.parse import quote

    return quote(str(e)[:200])


@router.post("/entreno/empezar")
def train_start(plan_id: int = Form(...), db: Session = Depends(get_db)):
    plan = _plan(db, plan_id)
    if not plan.items:
        return _back(plan, error=_err(ValueError(_("Agrega al menos un ejercicio."))))
    ws = tr.start_session(db, plan, source="web")
    missing = sorted(assist.today_missing(db, plan.date))
    ws.wa_context = {**(ws.wa_context or {}), "excluded_tags": missing}
    return _back()


@router.post("/entreno/items/{item_id}")
def train_item(item_id: int, action: str = Form("save"), sets: int = Form(3), rep_min: int = Form(8),
               rep_max: int = Form(12), rir: int = Form(2), weight_kg: str = Form(""),
               exercise_id: str = Form(""), next: str = Form(""), db: Session = Depends(get_db)):
    it = _item(db, item_id)
    plan = it.session
    try:
        if action == "remove":
            assist.remove(db, it)
        elif action == "up":
            assist.move(db, it, -1)
        elif action == "down":
            assist.move(db, it, 1)
        elif action == "swap":
            assist.swap(db, it, exercise_id)
        elif action == "reroll":
            assist.reroll(db, it)
        elif action == "done":  # ✓ as planned (Today or Train)
            assist.complete_as_planned(db, plan, it)
        elif action == "finish":  # done with it, whatever the set count
            ws = tr.active_session(db)
            if ws is None:
                raise ValueError(_("No hay un entreno en curso."))
            assist.finish_item(db, ws, it)
        elif action == "focus":
            ws = tr.active_session(db)
            if ws is None or ws.planned_session_id != plan.id:
                raise ValueError(_("No hay un entreno en curso."))
            assist.focus_on(db, ws, it)
        else:
            wk.update_item(db, it, sets=sets, rep_min=rep_min, rep_max=rep_max, rir=rir, weight_kg=form_num(weight_kg))
    except (ValueError, LookupError) as e:
        return _back(plan, error=_err(e))
    if next == "/":  # from Today
        return RedirectResponse("/", status_code=303)
    return _back(plan)


@router.get("/entreno/items/{item_id}/opciones", response_class=HTMLResponse)
def train_options(request: Request, item_id: int, db: Session = Depends(get_db)):
    it = _item(db, item_id)
    return render(request, "_train_options.html", "entrenar", item=it, options=assist.options(db, it), mode="swap")


@router.get("/entreno/planes/{plan_id}/buscar", response_class=HTMLResponse)
def train_search(request: Request, plan_id: int, q: str = "", db: Session = Depends(get_db)):
    plan = _plan(db, plan_id)
    ep = db.get(EquipmentProfile, plan.equipment_profile_id) if plan.equipment_profile_id else None
    tags = (set(ep.tags or []) if ep else None)
    if tags is not None:
        tags -= assist.today_missing(db, plan.date)
    found = catalog.search(db, q, profile_tags=tags, limit=12) if len(q.strip()) >= 2 else []
    return render(request, "_train_options.html", "entrenar", plan=plan, options=found, mode="add")


@router.post("/entreno/planes/{plan_id}/agregar")
def train_add(plan_id: int, exercise_id: str = Form(""), group: str = Form(""), db: Session = Depends(get_db)):
    plan = _plan(db, plan_id)
    try:
        assist.add(db, plan, exercise_id or None, group or None)
    except (ValueError, LookupError) as e:
        return _back(plan, error=_err(e))
    return _back(plan)


@router.post("/entreno/series")
def train_log(item_id: int = Form(...), reps: str = Form(""), weight_kg: str = Form(""), rir: str = Form(""),
              minutes: str = Form(""), level: str = Form(""), speed_kmh: str = Form(""), incline_pct: str = Form(""),
              steps: str = Form(""), distance_km: str = Form(""), db: Session = Depends(get_db)):
    it = _item(db, item_id)
    try:
        reps_n, kg, rir_n, mins = form_num(reps), form_num(weight_kg), form_num(rir), form_num(minutes)
        cardio = {"level": form_num(level), "speed_kmh": form_num(speed_kmh), "incline_pct": form_num(incline_pct),
                  "steps": form_int(steps), "distance_km": form_num(distance_km)}
    except ValueError as e:
        return _back(error=_err(e))
    ws = tr.active_session(db)
    if ws is None or ws.planned_session_id != it.planned_session_id:
        return _back(error=_err(ValueError(_("No hay un entreno en curso."))))
    if mins:
        if not 0 < mins <= 600:
            return _back(error=_err(ValueError(_("Valores fuera de rango"))))
        assist.log_cardio(db, ws, it, mins, cardio)
        return _back()
    elif reps_n:
        if not 1 <= reps_n <= 100 or (kg is not None and not 0 <= kg <= 500) or (rir_n is not None and not 0 <= rir_n <= 10):
            return _back(error=_err(ValueError(_("Valores fuera de rango"))))
        entry = SetEntry(reps=int(reps_n), weight_kg=kg or None, rir=rir_n)
    else:
        return _back(error=_err(ValueError(_("Escribe las repeticiones."))))
    assist.focus_on(db, ws, it)
    tr.log_sets(db, ws, it, [entry], raw_text="web")
    rest = 0 if mins else _rest_seconds(db, it)
    if rest and gym_chat():  # "rest is over" in the group too (rings with the phone locked), unless reports only
        from ..wa.session_flow import _schedule_rest

        _schedule_rest(db, ws, it, rest)
    return _back(descanso=rest)


@router.post("/entreno/series/{set_id}/borrar")
def train_unlog(set_id: int, db: Session = Depends(get_db)):
    st = db.get(WorkoutSet, set_id)
    ws = tr.active_session(db)
    if st is None or ws is None or st.session_id != ws.id:
        raise HTTPException(404)
    tr.delete_set(db, st)
    return _back()


@router.post("/entreno/siguiente")
def train_next(db: Session = Depends(get_db)):
    ws = tr.active_session(db)
    if ws is not None:
        tr.advance(db, ws)
    return _back()


@router.post("/entreno/nota")
def train_note(note: str = Form(""), sleep: str = Form(""), energy: str = Form(""), db: Session = Depends(get_db)):
    """How the day feels (sleep, energy, free text), saved with the workout in progress."""
    ws = tr.active_session(db)
    if ws is None:
        return _back(error=_err(ValueError(_("No hay un entreno en curso."))))
    try:
        sl, en = form_int(sleep), form_int(energy)
    except ValueError:
        sl = en = None
    assist.save_note(ws, note, sl if sl and 1 <= sl <= 5 else None, en if en and 1 <= en <= 5 else None)
    return _back(guardado=1)


@router.post("/entreno/terminar")
def train_finish(rpe: str = Form(""), partial: str = Form(""), db: Session = Depends(get_db)):
    ws = tr.active_session(db)
    if ws is None:
        return _back()
    try:
        n = form_int(rpe)
    except ValueError:
        n = None
    # partial (cardio only / cut short): the program's next day stays, so the missed work comes back
    tr.finish_session(db, ws, n if n and 1 <= n <= 10 else None, advance_rotation=not partial)
    if gym_chat():  # the day's report in the WhatsApp group
        from ..wa.outbox import enqueue_text
        from ..wa.session_flow import _summary_text

        db.flush()
        enqueue_text(db, _summary_text(db, ws), handler="summary")
    db.flush()
    return RedirectResponse(f"/entrenos/{ws.id}?fin=1", status_code=303)
