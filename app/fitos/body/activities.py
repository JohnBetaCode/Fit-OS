"""Activities a wearable recorded (WHOOP workouts): labels, totals per sport, the match with a Fit-OS session,
and how one compares with the same sport in the week before. Pure queries over `activities`."""

from __future__ import annotations

from datetime import date, timedelta
from statistics import fmean

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import Activity, WorkoutSession
from ..i18n import _

# WHOOP sport slugs → (Spanish label, icon). Unknown slugs are shown title-cased.
SPORTS: dict[str, tuple[str, str]] = {
    "walking": ("Caminar", "🚶"), "running": ("Correr", "🏃"), "powerlifting": ("Powerlifting", "🏋️"),
    "weightlifting": ("Pesas", "🏋️"), "functional-fitness": ("Entrenamiento funcional", "🤸"),
    "elliptical": ("Elíptica", "〰️"), "stairmaster": ("Escaladora", "🪜"), "rowing": ("Remo", "🚣"),
    "hiking-rucking": ("Senderismo", "🥾"), "cycling": ("Ciclismo", "🚴"), "spin": ("Spinning", "🚴"),
    "swimming": ("Natación", "🏊"), "yoga": ("Yoga", "🧘"), "pilates": ("Pilates", "🧘"),
    "stretching": ("Estiramientos", "🤸"), "hiit": ("HIIT", "🔥"), "boxing": ("Boxeo", "🥊"),
    "soccer": ("Fútbol", "⚽"), "basketball": ("Baloncesto", "🏀"), "tennis": ("Tenis", "🎾"),
    "padel": ("Pádel", "🎾"), "climbing": ("Escalada", "🧗"), "dance": ("Baile", "💃"),
    "cleaning": ("Limpieza", "🧹"), "meditation": ("Meditación", "🧘"), "activity": ("Actividad", "⚡"),
}
STRENGTH = {"powerlifting", "weightlifting", "functional-fitness"}
ZONE_LABELS = ["<50 %", "50–60 %", "60–70 %", "70–80 %", "80–90 %", "90–100 %"]
ZONE_COLORS = ["#4b5563", "#9fd0ff", "#5aa9ff", "#f5b942", "#ff8c42", "#ff6b6b"]
MATCH_SLACK = timedelta(minutes=20)


def sport_label(slug: str) -> str:
    es = SPORTS.get(slug, (slug.replace("-", " ").capitalize(), ""))[0]
    return _(es) if slug in SPORTS else es


def sport_icon(slug: str) -> str:
    return SPORTS.get(slug, ("", "⚡"))[1]


def minutes(a: Activity) -> float:
    return (a.ended_at - a.started_at).total_seconds() / 60


def listing(db: Session, source: str, since: date | None = None, sport: str | None = None) -> list[Activity]:
    q = select(Activity).where(Activity.source == source)
    if since is not None:
        q = q.where(Activity.local_date >= since)
    if sport:
        q = q.where(Activity.sport == sport)
    return list(db.scalars(q.order_by(Activity.started_at.desc())))


def totals(acts: list[Activity]) -> list[dict]:
    """Per sport: count, hours, energy, mean strain and zone minutes, most frequent first."""
    by: dict[str, list[Activity]] = {}
    for a in acts:
        by.setdefault(a.sport, []).append(a)
    out = []
    for sport, xs in by.items():
        zones = [0.0] * 6
        for a in xs:
            for i, z in enumerate(a.zones_min or []):
                zones[i] += z
        strains = [a.strain for a in xs if a.strain is not None]
        out.append({"sport": sport, "label": sport_label(sport), "icon": sport_icon(sport), "n": len(xs),
                    "hours": round(sum(minutes(a) for a in xs) / 60, 1),
                    "kcal": round(sum(a.kcal or 0 for a in xs)), "strain": round(fmean(strains), 1) if strains else None,
                    "zones": [round(z) for z in zones]})
    return sorted(out, key=lambda t: (-t["n"], t["label"]))


def for_session(db: Session, ws: WorkoutSession) -> Activity | None:
    """The wearable activity that overlaps a Fit-OS session the most (WHOOP's record of that workout)."""
    start = ws.started_at
    end = ws.ended_at or ws.last_activity_at or start
    if start is None:
        return None
    cands = db.scalars(select(Activity).where(Activity.started_at <= end + MATCH_SLACK,
                                              Activity.ended_at >= start - MATCH_SLACK))
    best, best_overlap = None, 0.0
    for a in cands:
        overlap = (min(a.ended_at, end + MATCH_SLACK) - max(a.started_at, start - MATCH_SLACK)).total_seconds()
        bonus = 1.2 if a.sport in STRENGTH else 1.0  # the lifting record over a walk to the gym
        if overlap * bonus > best_overlap:
            best, best_overlap = a, overlap * bonus
    return best


def compare(db: Session, a: Activity) -> dict:
    """Mean of the same sport in the 7 days before (WHOOP's "vs 1 week past"); 28 days if there was none."""
    for days in (7, 28):
        prev = list(db.scalars(select(Activity).where(
            Activity.source == a.source, Activity.sport == a.sport, Activity.id != a.id,
            Activity.started_at < a.started_at, Activity.local_date >= a.local_date - timedelta(days=days))))
        if prev:
            return {"days": days, "n": len(prev), "max_hr": _mean_of(prev, lambda x: x.max_hr),
                    "avg_hr": _mean_of(prev, lambda x: x.avg_hr), "kcal": _mean_of(prev, lambda x: x.kcal),
                    "minutes": _mean_of(prev, minutes), "strain": _mean_of(prev, lambda x: x.strain)}
    return {"days": None, "n": 0}


def _mean_of(acts: list[Activity], f) -> float | None:
    vals = [f(x) for x in acts if f(x) is not None]
    return round(fmean(vals), 1) if vals else None


def detail(a: Activity) -> dict:
    from ..core.timeutil import to_local  # noqa: PLC0415

    zones = a.zones_min or [0] * 6
    total = sum(zones) or 1
    return {"label": sport_label(a.sport), "icon": sport_icon(a.sport), "minutes": round(minutes(a)),
            "start": to_local(a.started_at).strftime("%H:%M"), "end": to_local(a.ended_at).strftime("%H:%M"),
            "has_zones": bool(a.zones_min) and sum(zones) > 0,
            "zones": [{"label": ZONE_LABELS[i], "color": ZONE_COLORS[i], "min": round(z, 1),
                       "pct": round(z / total * 100)} for i, z in enumerate(zones)],
            "km": round(a.distance_m / 1000, 2) if a.distance_m else None}


def by_day(db: Session, source: str, since: date) -> dict[date, dict]:
    """Day → energy spent in activities and the hardest activity's strain (for the Rest charts)."""
    out: dict[date, dict] = {}
    for a in db.scalars(select(Activity).where(Activity.source == source, Activity.local_date >= since)):
        d = out.setdefault(a.local_date, {"kcal": 0.0, "top_strain": None, "top_sport": None})
        d["kcal"] += a.kcal or 0
        if a.strain is not None and (d["top_strain"] is None or a.strain > d["top_strain"]):
            d["top_strain"], d["top_sport"] = a.strain, a.sport
    return out
