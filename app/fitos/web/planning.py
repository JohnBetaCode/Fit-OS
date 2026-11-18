"""Vista y edición del programa activo (Ajustes → Programa)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..training import service as tr


def program_view(db: Session) -> dict:
    from ..training.planner import load_templates

    try:
        templates = load_templates()
    except Exception:  # noqa: BLE001
        templates = {}
    prog = tr.active_program(db)
    names = {k: (v.get("name") if isinstance(v, dict) else k) for k, v in (templates.get("templates") or {}).items()}
    return {"program": prog, "templates": names,
            "days": [{"name": d.name, "slots": d.slots, "next": i == (prog.rotation_idx if prog else -1)}
                     for i, d in enumerate(prog.days)] if prog else []}


def apply_program_form(db: Session, form: dict) -> None:
    from ..body.service import get_profile
    from ..training.planner import create_program_from_template

    key = form.get("template_key")
    if key:
        create_program_from_template(db, key, get_profile(db).days_per_week)
    prog = tr.active_program(db)
    if prog and form.get("target_rir") not in (None, ""):
        rir = max(0, min(5, int(form["target_rir"])))
        for d in prog.days:
            d.slots = [{**s, "target_rir": rir} for s in d.slots]
        prog.config = {**(prog.config or {}), "target_rir": rir}
