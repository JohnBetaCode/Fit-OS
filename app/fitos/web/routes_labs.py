"""Labs tab: overview, per-marker detail, nutrition and review of doubtful values."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.db import get_db
from ..core.models import Biomarker, Profile
from ..i18n import _
from ..labs import service
from .ui import render

router = APIRouter()


def _who(db: Session) -> tuple[str, date | None]:
    p = db.get(Profile, 1)
    return ((p.sex or "male") if p else "male"), (p.birth_date if p else None)


def _done(request: Request) -> Response:
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Refresh": "true"})
    return RedirectResponse(request.headers.get("referer") or "/examenes", status_code=303)


@router.get("/examenes", response_class=HTMLResponse)
async def labs_overview(request: Request, db: Session = Depends(get_db)):
    sex, birth = _who(db)
    keys = db.scalars(select(Biomarker).order_by(Biomarker.sort_order)).all()
    return render(request, "labs/overview.html", "examenes", ov=service.overview(db, sex, birth), sub="resumen",
                  markers=keys)


@router.get("/examenes/nutricion", response_class=HTMLResponse)
async def labs_nutrition(request: Request, db: Session = Depends(get_db)):
    sex, birth = _who(db)
    return render(request, "labs/nutrition.html", "examenes", items=service.nutrition(db, sex, birth),
                  sub="nutricion")


@router.get("/examenes/{key}", response_class=HTMLResponse)
async def labs_detail(request: Request, key: str, db: Session = Depends(get_db)):
    sex, birth = _who(db)
    d = service.marker_detail(db, key, sex, birth)
    if d is None:
        raise HTTPException(404, _("Marcador desconocido"))
    return render(request, "labs/detail.html", "examenes", d=d, sub="resumen", age_known=birth is not None)


@router.post("/examenes/resultados/{result_id}/confirmar")
async def labs_confirm(request: Request, result_id: int, db: Session = Depends(get_db)):
    if service.confirm_result(db, result_id) is None:
        raise HTTPException(404)
    return _done(request)


@router.post("/examenes/resultados/{result_id}/corregir")
async def labs_correct(
    request: Request, result_id: int, value: str = Form(...), key: str = Form(""), db: Session = Depends(get_db)
):
    try:
        v = float(value.replace(",", "."))
    except ValueError:
        raise HTTPException(422, _("Valor no numérico")) from None
    try:
        r = service.correct_result(db, result_id, v, key or None)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    if r is None:
        raise HTTPException(404)
    return _done(request)
