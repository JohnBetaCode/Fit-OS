"""Where data comes from: each integration's name and icon, shown as a small badge (linking to its card on the
Integrations page) next to every chart, card or line it feeds. Data typed by the person has no badge.

A new integration adds its `source` here (the value it writes to `body_metrics.source`).
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import BodyMetric

META = {"whoop": {"name": "WHOOP", "icon": "⌚"}, "renpho": {"name": "Renpho", "icon": "⚖️"}}


def meta(source: str | None) -> dict | None:
    m = META.get(source or "")
    return {**m, "source": source, "href": f"/integraciones#{source}"} if m else None


def used(db: Session, kinds: list[str], since: date | None = None) -> list[str]:
    """The integrations that wrote any of these kinds (since a day), in a stable order."""
    q = select(BodyMetric.source).where(BodyMetric.kind.in_(kinds), BodyMetric.source.in_(list(META))).distinct()
    if since is not None:
        q = q.where(BodyMetric.measured_on >= since)
    found = set(db.scalars(q))
    return [s for s in META if s in found]
