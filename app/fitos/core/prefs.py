"""Preferencias editables en caliente (tabla `settings`), con valores por defecto en código."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import Setting

DEFAULTS: dict[str, Any] = {
    "morning_hour": "06:30",  # mensaje proactivo con el plan del día
    "morning_enabled": True,
    "quiet_start": "21:00",
    "quiet_end": "06:00",
    "rest_seconds_compound": 150,
    "rest_seconds_isolation": 90,
    "clip_policy": "first_time",  # first_time | always | on_demand
    "weekly_summary_enabled": True,  # Sunday summary in the group
    "weekly_summary_hour": "19:00",
    "warmup_sets": True,  # suggest a warm-up ramp on the first heavy lift of each muscle
    "rest_alert": True,  # "rest is over" message after each set
    "wa_cards": "one",  # one: one exercise card at a time | all: every card when the session starts
    "llm_daily_budget_usd": 1.0,
    "session_idle_nudge_min": 45,
    "session_idle_close_min": 90,
    "weekly_sets_target_min": 10,
    "weekly_sets_target_max": 20,
    "language": None,  # es | en; None = DEFAULT_LANGUAGE
    "assist_last": None,  # last Train setup (place, focus, missing equipment…) and the day it applies to
}


def get(db: Session, key: str) -> Any:
    row = db.get(Setting, key)
    if row is None:
        return DEFAULTS.get(key)
    return row.value


def set_(db: Session, key: str, value: Any) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
    if key == "language":
        from ..i18n import set_lang  # noqa: PLC0415

        set_lang(value)


def all_prefs(db: Session) -> dict[str, Any]:
    out = dict(DEFAULTS)
    for row in db.query(Setting).all():
        out[row.key] = row.value
    return out
