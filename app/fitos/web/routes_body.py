"""Body map (Progress → Body): recovery per muscle and this week's hard sets per muscle, on a front/back figure."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ..catalog.service import muscle_label
from ..core import prefs
from ..core.db import get_db
from ..core.timeutil import local_today
from ..i18n import _
from ..training import assist, metrics, recovery
from ..training.week import week_start
from .ui import render

router = APIRouter()


def status_labels() -> dict[str, str]:
    return {"ready": _("Listo"), "fatigued": _("Fatigado"), "recovering": _("En recuperación")}


def level_labels() -> dict[str, str]:
    return {"none": _("Sin entrenar"), "low": _("Poco"), "mid": _("Casi en la meta"), "target": _("En la meta"),
            "over": _("Por encima de la meta")}


def _since(r: recovery.MuscleRecovery) -> str:
    d = r.days_since
    if d is None:
        return _("sin registros recientes")
    return _("hoy") if d == 0 else _("ayer") if d == 1 else _("hace {n} días", n=d)


def recovery_view(db: Session) -> dict:
    rows = recovery.recovery(db)
    st = status_labels()
    fills = {r.key: f"var(--map-{r.status})" for r in rows}
    tips = {r.key: f"{muscle_label(r.key)}: {st[r.status]}" for r in rows}
    items = [{"key": r.key, "label": muscle_label(r.key), "status": r.status, "status_label": st[r.status],
              "since": _since(r), "ready_in_h": r.ready_in_h, "trained": r.last_trained is not None} for r in rows]
    groups = assist.group_labels()
    ready = recovery.ready_groups(rows, assist.GROUPS)
    return {"fills": fills, "tips": tips, "items": items, "ready": [(g, groups[g]) for g in ready]}


def week_view(db: Session) -> dict:
    lo, hi = int(prefs.get(db, "weekly_sets_target_min")), int(prefs.get(db, "weekly_sets_target_max"))
    sets = metrics.weekly_hard_sets_by_muscle(db, week_start(local_today()))
    levels = recovery.week_levels(sets, lo, hi)
    lab = level_labels()
    fills = {k: f"var(--map-{lvl})" for k, lvl in levels.items()}
    tips = {k: f"{muscle_label(k)}: {_('{n} series duras', n=f'{sets.get(k, 0):g}')}" for k in levels}
    items = sorted(({"key": k, "label": muscle_label(k), "level": lvl, "level_label": lab[lvl],
                     "sets": round(float(sets.get(k, 0.0)), 1), "missing": max(0.0, lo - float(sets.get(k, 0.0))),
                     "pct": min(100, float(sets.get(k, 0.0)) / hi * 100)} for k, lvl in levels.items()),
                   key=lambda i: (i["sets"], i["label"]))
    return {"fills": fills, "tips": tips, "items": items, "lo": lo, "hi": hi}


@router.get("/progreso/cuerpo", response_class=HTMLResponse)
def body_page(request: Request, vista: str = "recuperacion", db: Session = Depends(get_db)):
    view = "semana" if vista == "semana" else "recuperacion"
    data = recovery_view(db) if view == "recuperacion" else week_view(db)
    return render(request, "body_map.html", "progreso", view=view, levels=level_labels(),
                  statuses=status_labels(), **data)
