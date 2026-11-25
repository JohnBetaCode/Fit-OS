"""Wearables: which one feeds readiness, sleep and the Rest tab, and whether it's healthy.

Everything built on wearable data (the readiness line and illness warning in the morning message and Today,
the Progress ring, the Rest tab) shows only while a wearable is linked, its last sync worked and it has sent
something recently. When a membership ends and syncs start failing, those features hide themselves.

A wearable integration (WHOOP today; Oura, Garmin… tomorrow) is a module with `SOURCE`, `STATUS_PREF` and
`linked()` that writes the shared kinds (`recovery_pct`, `hrv_ms`, `resting_hr`, `sleep_hours`, …) to
`body_metrics`: add it to `WEARABLES` and every feature works with it unchanged.
"""

from __future__ import annotations

from datetime import date, timedelta
from types import ModuleType

from sqlalchemy.orm import Session

from ..core import prefs
from . import whoop

WEARABLES: tuple[ModuleType, ...] = (whoop,)
FRESH_DAYS = 5  # no data for longer than this: not active (not worn, or the account stopped answering)


def active_source(db: Session, today: date | None = None) -> str | None:
    """The source name of the first wearable that is linked, synced without error and recent; else None."""
    from ..core.timeutil import local_today  # noqa: PLC0415

    today = today or local_today()
    for w in WEARABLES:
        st = prefs.get(db, w.STATUS_PREF) or {}
        if not w.linked() or st.get("last_error"):
            continue
        try:
            latest = date.fromisoformat(st.get("latest") or "")
        except ValueError:
            continue
        if today - latest <= timedelta(days=FRESH_DAYS):
            return w.SOURCE
    return None
