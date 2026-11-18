"""Welcome flow (`/bienvenida`): the first sign-in of a new person walks through what the planner needs.

1. About you (name, sex, birth date, height, weight) · 2. Goal, experience and injuries · 3. Training week
(days, time, place, session length) · 4. Program (the template recommended for those days, or another) · done.

Each step saves on submit, so leaving halfway keeps what was filled in. Finishing step 4 marks the profile
`onboarded`; until then Today sends a new person here. People who already trained were marked by the
`7c1e0b5d9a2f` migration and never see it (they can still open it from Profile).
"""

from __future__ import annotations

import contextlib
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..body import service as body
from ..core.db import get_db
from ..i18n import _
from ..training import service as tr
from .ui import render

router = APIRouter()
STEPS = 4


def recommended_template(templates: dict, days: int) -> str:
    dfd = templates.get("default_for_days") or {}
    keys = sorted(int(k) for k in dfd)
    if not keys:
        return next(iter(templates.get("templates") or {}), "")
    d = min(max(days, keys[0]), keys[-1])
    return dfd.get(d) or dfd.get(str(d)) or ""


def _templates() -> dict:
    from ..training.planner import load_templates  # noqa: PLC0415

    try:
        return load_templates()
    except Exception:  # noqa: BLE001
        return {"templates": {}, "default_for_days": {}}


def _step_titles() -> list[str]:
    return [_("Sobre ti"), _("Tu objetivo"), _("Tu semana"), _("Tu programa")]


@router.get("/bienvenida", response_class=HTMLResponse)
def welcome(request: Request, paso: int = 1, db: Session = Depends(get_db)):
    prof = body.ensure_defaults(db)
    step = min(max(paso, 1), STEPS + 1)
    acc = getattr(request.state, "account", None)
    ctx: dict = {}
    if step == 4:
        tpl = _templates()
        rec = recommended_template(tpl, prof.days_per_week)
        active = tr.active_program(db)
        ctx["options"] = [
            {"key": k, "name": v.get("name", k), "days": [d["name"] for d in v.get("days") or []],
             "per_week": v.get("days_per_week"), "recommended": k == rec}
            for k, v in (tpl.get("templates") or {}).items()
        ]
        ctx["options"].sort(key=lambda o: not o["recommended"])
        ctx["chosen"] = active.template_key if active else rec
    if step == STEPS + 1:
        ctx["program"] = tr.active_program(db)
        ctx["place"] = body.equipment_name(body.default_equipment(db)) if body.default_equipment(db) else ""
    return render(
        request, "welcome.html", "", bare=True, step=step, steps=_step_titles(), p=prof, weight=body.latest_metric(db),
        default_name=prof.display_name or (acc.display_name if acc else ""), sexes=body.labels("sex"),
        goals=body.labels("goal"), levels=body.labels("experience"), joints=body.labels("joint"),
        weekdays=body.WEEKDAYS, weekday_names=body.labels("weekday"), eps=body.equipment_profiles(db),
        first_time=next(iter((prof.schedule or {}).values()), {}).get("time", "06:30"),
        first_ep=next(iter((prof.schedule or {}).values()), {}).get("equipment_profile_id"),
        **ctx,
    )


@router.post("/bienvenida/saltar")
def welcome_skip(db: Session = Depends(get_db)):
    """Later: the planner works with the defaults; Profile has everything."""
    body.ensure_defaults(db).onboarded = True
    return RedirectResponse("/", status_code=303)


@router.post("/bienvenida/{step}")
async def welcome_save(step: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    prof = body.ensure_defaults(db)
    if step == 1:
        prof.display_name = (form.get("display_name") or "").strip()[:64] or None
        if form.get("sex") in body.labels("sex"):
            prof.sex = form["sex"]
        with contextlib.suppress(ValueError):
            prof.birth_date = date.fromisoformat(form.get("birth_date") or "") if form.get("birth_date") else None
        with contextlib.suppress(ValueError):
            prof.height_cm = float(str(form["height_cm"]).replace(",", ".")) if form.get("height_cm") else None
        if form.get("weight_kg"):
            with contextlib.suppress(ValueError):
                body.log_metric(db, "weight_kg", float(str(form["weight_kg"]).replace(",", ".")))
    elif step == 2:
        if form.get("primary_goal") in body.labels("goal"):
            prof.primary_goal = form["primary_goal"]
        if form.get("experience") in body.labels("experience"):
            prof.experience = form["experience"]
        prof.goal_notes = (form.get("goal_notes") or "").strip() or None
        prof.injuries = [{"area": j, "severity": "leve", "active": True, "notes": (form.get("injury_notes") or "").strip()}
                         for j in body.labels("joint") if form.get(f"inj_{j}")]
    elif step == 3:
        days = [d for d in body.WEEKDAYS if form.get(f"day_{d}")]
        if not days:
            return RedirectResponse("/bienvenida?paso=3&error=dias", status_code=303)
        ep_id = int(form.get("equipment_profile_id") or 0) or None
        time = form.get("time") or "06:30"
        prof.schedule = {d: {"equipment_profile_id": ep_id, "time": time} for d in days}
        prof.days_per_week = len(days)
        with contextlib.suppress(ValueError):
            prof.session_minutes = max(20, min(180, int(form.get("session_minutes") or prof.session_minutes)))
        if ep_id:
            for ep in body.equipment_profiles(db):
                ep.is_default = ep.id == ep_id
    elif step == 4:
        from ..training.planner import create_program_from_template  # noqa: PLC0415

        key = form.get("template_key") or recommended_template(_templates(), prof.days_per_week)
        active = tr.active_program(db)
        if not active or active.template_key != key:
            create_program_from_template(db, key, prof.days_per_week)
        prof.onboarded = True
    else:
        return RedirectResponse("/bienvenida", status_code=303)
    return RedirectResponse(f"/bienvenida?paso={step + 1}", status_code=303)
