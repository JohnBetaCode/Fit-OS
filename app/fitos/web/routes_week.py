"""Week tab: the seven days, building or resting a day, and editing a day's plan before it starts."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..body import service as body
from ..catalog import service as catalog
from ..core.db import get_db
from ..core.models import EquipmentProfile, PlannedItem, PlannedSession
from ..core.timeutil import local_today
from ..i18n import _
from ..training import week as wk
from ..wa import cards
from .ui import form_int, form_num, render

router = APIRouter()


def _back(on: date, anchor: str = "") -> RedirectResponse:
    return RedirectResponse(f"/semana?d={wk.week_start(on).isoformat()}{anchor}", status_code=303)


def _fail(e: Exception) -> HTTPException:
    return HTTPException(404 if isinstance(e, LookupError) else 400, str(e))


@router.get("/semana", response_class=HTMLResponse)
def week_page(request: Request, d: str | None = None, db: Session = Depends(get_db)):
    body.ensure_defaults(db)
    start = wk.week_start(date.fromisoformat(d) if d else local_today())
    view = wk.week_view(db, start)
    from ..core import prefs

    lo, hi = int(prefs.get(db, "weekly_sets_target_min")), int(prefs.get(db, "weekly_sets_target_max"))
    return render(request, "week.html", "semana", view=view, prev=start - timedelta(days=7),
                  next=start + timedelta(days=7), this_week=wk.week_start(local_today()), lo=lo, hi=hi,
                  profiles=body.equipment_profiles(db), w=cards.weight_text, rep_range=cards.rep_range,
                  is_timed=cards.is_timed, day_kinds=_day_kinds())


def _day_kinds() -> list[tuple[str, str, str]]:
    from ..body import days

    return [(k, days.KIND_ICON[k], days.label(k)) for k in days.KINDS]


@router.post("/semana/dia")
def week_day(on: str = Form(..., alias="date"), action: str = Form("build"), program_day_id: str = Form(""),
             equipment_profile_id: str = Form(""), db: Session = Depends(get_db)):
    day = date.fromisoformat(on)
    try:
        program_day_id, equipment_profile_id = form_int(program_day_id), form_int(equipment_profile_id)
        if action == "rest":
            wk.rest_day(db, day)
        else:
            wk.build_day(db, day, program_day_id, equipment_profile_id)
    except (ValueError, LookupError) as e:
        raise _fail(e) from e
    return _back(day, f"#d-{day.isoformat()}")


@router.post("/semana/estado")
def week_day_status(on: str = Form(..., alias="date"), kind: str = Form(""), db: Session = Depends(get_db)):
    """Mark a day as sick / injured / travel, or clear it (empty `kind`)."""
    from ..body import days

    day = date.fromisoformat(on)
    if day > local_today():
        raise HTTPException(400, _("La fecha no puede ser futura"))
    if kind:
        if kind not in days.KINDS:
            raise HTTPException(400)
        days.mark(db, day, kind, source="web")
    else:
        days.clear(db, day)
    return _back(day, f"#d-{day.isoformat()}")


def _item(db: Session, item_id: int) -> PlannedItem:
    it = db.get(PlannedItem, item_id)
    if it is None:
        raise HTTPException(404)
    return it


@router.post("/semana/items/{item_id}")
def week_item(item_id: int, action: str = Form("save"), sets: int = Form(3), rep_min: int = Form(8),
              rep_max: int = Form(12), rir: int = Form(2), weight_kg: str = Form(""),
              minutes: str = Form(""), exercise_id: str = Form(""), db: Session = Depends(get_db)):
    it = _item(db, item_id)
    day = it.session.date
    try:
        if action == "remove":
            wk.remove_item(db, it)
        elif action == "up":
            wk.move_item(db, it, -1)
        elif action == "down":
            wk.move_item(db, it, 1)
        elif action == "swap":
            wk.swap_item(db, it, exercise_id)
        else:
            wk.update_item(db, it, sets=sets, rep_min=rep_min, rep_max=rep_max, rir=rir, weight_kg=form_num(weight_kg),
                           minutes=form_num(minutes))
    except (ValueError, LookupError) as e:
        raise _fail(e) from e
    return _back(day, f"#d-{day.isoformat()}")


@router.post("/semana/planes/{plan_id}/items")
def week_add(plan_id: int, exercise_id: str = Form(...), db: Session = Depends(get_db)):
    plan = db.get(PlannedSession, plan_id)
    if plan is None:
        raise HTTPException(404)
    try:
        wk.add_item(db, plan, exercise_id)
    except (ValueError, LookupError) as e:
        raise _fail(e) from e
    return _back(plan.date, f"#d-{plan.date.isoformat()}")


@router.get("/semana/items/{item_id}/opciones", response_class=HTMLResponse)
def week_swap_options(request: Request, item_id: int, db: Session = Depends(get_db)):
    from ..training import service as tr

    it = _item(db, item_id)
    alts = [e for e, _score in tr.alternatives(db, None, it, limit=8)]
    return render(request, "_week_options.html", "semana", item=it, options=alts, mode="swap")


@router.get("/semana/planes/{plan_id}/buscar", response_class=HTMLResponse)
def week_search(request: Request, plan_id: int, q: str = "", db: Session = Depends(get_db)):
    plan = db.get(PlannedSession, plan_id)
    if plan is None:
        raise HTTPException(404)
    ep = db.get(EquipmentProfile, plan.equipment_profile_id) if plan.equipment_profile_id else None
    found = catalog.search(db, q, profile_tags=ep.tags if ep else None, limit=12) if len(q.strip()) >= 2 else []
    return render(request, "_week_options.html", "semana", plan=plan, options=found, mode="add")
