"""Exercises tab: filterable grid (HTMX) and detail with clip, steps and actions."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..catalog import service
from ..core.db import get_db
from ..core.models import EquipmentProfile
from .ui import render

router = APIRouter()

PAGE_SIZE = 48



def _labels() -> dict[str, dict[str, str]]:
    """Label tables in the current language (same keys and order as the Spanish ones)."""
    return {
        "muscle_es": service.labels(service.MUSCLE_ES),
        "equipment_es": service.labels(service.EQUIPMENT_ES),
        "pattern_es": service.labels(service.PATTERN_ES),
        "joint_es": service.labels(service.JOINT_ES),
        "mechanic_es": service.labels(service.MECHANIC_ES),
        "laterality_es": service.labels(service.LATERALITY_ES),
    }

# Gancho para "Tu historial": training registra aquí una función (db, exercise_id) -> list[dict] con
# claves `date`, `weight_kg`, `reps`, `e1rm_kg` (más reciente primero). Sin proveedor no se muestra tabla.
HistoryProvider = Callable[[Session, str], list[dict[str, Any]]]
_history_provider: HistoryProvider | None = None


def set_history_provider(fn: HistoryProvider | None) -> None:
    global _history_provider
    _history_provider = fn


def _flag(v: str | None) -> bool:
    return v in ("1", "on", "true")


@router.get("/ejercicios", response_class=HTMLResponse)
async def exercises(
    request: Request,
    q: str = "",
    muscle: str = "",
    equipment: str = "",
    profile: str = "",
    pattern: str = "",
    staples: str | None = None,
    favs: str | None = None,
    offset: int = 0,
    frag: str = "",
    db: Session = Depends(get_db),
):
    profiles = list(db.scalars(select(EquipmentProfile).order_by(EquipmentProfile.name)))
    chosen = next((p for p in profiles if str(p.id) == profile), None)
    include_disabled = not service.any_enabled(db)  # sin enriquecimiento aún: mostrar todo
    filters = {
        "q": q.strip(),
        "muscle": muscle,
        "equipment": equipment,
        "profile": profile if chosen else "",
        "pattern": pattern,
        "staples": "1" if _flag(staples) else "",
        "favs": "1" if _flag(favs) else "",
    }
    offset = max(offset, 0)
    rows = service.search(
        db,
        q=filters["q"],
        muscle=muscle or None,
        equipment_tag=equipment or None,
        profile_tags=chosen.tags if chosen else None,
        pattern=pattern or None,
        staples_only=bool(filters["staples"]),
        favorites_only=bool(filters["favs"]),
        limit=PAGE_SIZE + 1,
        offset=offset,
        include_disabled=include_disabled,
    )
    qs = urlencode({k: v for k, v in filters.items() if v})
    ctx = {
        "items": rows[:PAGE_SIZE],
        "next_offset": offset + PAGE_SIZE if len(rows) > PAGE_SIZE else None,
        "qs": qs,
        "filters": filters,
        "profiles": profiles,
        "include_disabled": include_disabled,
        **_labels(),
    }
    if frag == "items":
        return render(request, "exercises/_items.html", "ejercicios", **ctx)
    if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"):
        return render(request, "exercises/_results.html", "ejercicios", **ctx)
    return render(request, "exercises/index.html", "ejercicios", **ctx)


def _detail_ctx(db: Session, exercise_id: str) -> dict[str, Any]:
    ex = service.get(db, exercise_id)
    if ex is None:
        raise HTTPException(404)
    return {"ex": ex, "disliked": exercise_id in service.disliked_ids(db)}


@router.get("/ejercicios/{exercise_id}", response_class=HTMLResponse)
async def exercise_detail(request: Request, exercise_id: str, db: Session = Depends(get_db)):
    ctx = _detail_ctx(db, exercise_id)
    history = _history_provider(db, exercise_id) if _history_provider else None
    return render(request, "exercises/detail.html", "ejercicios", history=history, **ctx, **_labels())


def _toggle(request: Request, db: Session, exercise_id: str, fn: Callable[[Session, str], bool]):
    try:
        fn(db, exercise_id)
    except KeyError:
        raise HTTPException(404) from None
    return render(request, "exercises/_actions.html", "ejercicios", **_detail_ctx(db, exercise_id))


@router.post("/ejercicios/{exercise_id}/favorito", response_class=HTMLResponse)
async def exercise_favorite(request: Request, exercise_id: str, db: Session = Depends(get_db)):
    return _toggle(request, db, exercise_id, service.toggle_favorite)


@router.post("/ejercicios/{exercise_id}/descartar", response_class=HTMLResponse)
async def exercise_dislike(request: Request, exercise_id: str, db: Session = Depends(get_db)):
    return _toggle(request, db, exercise_id, service.toggle_disliked)
