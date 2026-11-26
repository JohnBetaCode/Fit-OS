"""Progress → Rest: a wearable's recovery, HRV, sleep and strain as chart-ready series.

Pure shaping over `body_metrics` rows of the active wearable (`readiness.source`); the page draws them. Numbers
the person can check: 7-day averages against their own previous 60 days, never population norms.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import WorkoutSession
from ..i18n import _
from . import activities, readiness
from .readiness import fmean, fsd

RANGES = {"30d": 30, "90d": 90, "1a": 365, "todo": None}
BASE_DAYS = 60
# (kind, label, unit, decimals, higher is better) for the last-night card
LAST_NIGHT = [("sleep_hours", "Horas de sueño", "h", 2, True), ("in_bed_h", "Tiempo en la cama", "h", 2, True),
              ("sleep_performance_pct", "Rendimiento", "%", 0, True), ("sleep_efficiency_pct", "Eficiencia", "%", 0, True),
              ("sleep_consistency_pct", "Consistencia", "%", 0, True),
              ("sleep_disturbances", "Interrupciones", "", 0, False),
              ("respiratory_rate", "Frecuencia respiratoria", "rpm", 1, False), ("sleep_debt_h", "Deuda de sueño", "h", 2, False)]
SLEEP_BUCKETS = [(0, 6, "< 6 h"), (6, 7, "6–7 h"), (7, 8, "7–8 h"), (8, 9, "8–9 h"), (9, 24, "≥ 9 h")]
WEEKLY_AFTER = 120  # longer ranges draw sleep as weekly means: a bar per night stops being readable


def range_labels() -> list[tuple[str, str]]:
    return [("30d", _("30 días")), ("90d", _("90 días")), ("1a", _("1 año")), ("todo", _("Todo"))]


def _mean(xs: list[float]) -> float | None:
    return fmean(xs) if xs else None


def _r(v: float | None, nd: int = 1) -> float | None:
    return None if v is None else round(v, nd)


def _series(rows: dict[date, dict[str, float]], kind: str) -> tuple[list[date], list[float]]:
    ds = sorted(d for d in rows if kind in rows[d])
    return ds, [rows[d][kind] for d in ds]


def _back(ds: list[date], vals: list[float], d: date, lo: int, hi: int) -> list[float]:
    """Values from `lo` to `hi` days before d (lo inclusive, hi exclusive): bisect, not a scan."""
    a, b = bisect_left(ds, d - timedelta(days=hi - 1)), bisect_right(ds, d - timedelta(days=lo))
    return vals[a:b]


def _rolling(rows: dict[date, dict[str, float]], kind: str, days: list[date], window: int) -> list[list]:
    """[iso, mean of the last `window` days that have the value], one point per day with data."""
    ds, vals = _series(rows, kind)
    return [[d.isoformat(), round(fmean(_back(ds, vals, d, 0, window)), 1)]
            for d in days if kind in rows.get(d, {})]


def _window(rows: dict[date, dict[str, float]], kind: str, end: date, start_back: int, days: int) -> float | None:
    return _mean([v[kind] for d, v in rows.items() if start_back <= (end - d).days < start_back + days and kind in v])


def build(db: Session, today: date, rango: str = "90d") -> dict:
    src = readiness.source(db)
    if src is None:
        return {"active": False}
    n = RANGES.get(rango, 90)
    every = readiness.rows_between(db, src, date(2000, 1, 1), today)
    if not every:
        return {"active": True, "empty": True}
    first = min(every)
    start = max(first, today - timedelta(days=n - 1)) if n else first
    days = sorted(d for d in every if d >= start)
    rows = {d: every[d] for d in days}
    iso = [d.isoformat() for d in days]

    def col(kind: str, nd: int = 1) -> list[list]:
        return [[d.isoformat(), round(rows[d][kind], nd)] for d in days if kind in rows[d]]

    trained = {s.date for s in db.scalars(select(WorkoutSession).where(WorkoutSession.date >= start))}

    # HRV band: your previous 60 days' mean ± 1 SD, day by day
    hrv_band = []
    hds, hvals = _series(every, "hrv_ms")
    for d in days:
        prev = _back(hds, hvals, d, 1, BASE_DAYS + 1)
        if len(prev) >= 14:
            mu, sd = fmean(prev), fsd(prev)
            hrv_band.append([d.isoformat(), round(mu - sd, 1), round(2 * sd, 1)])

    # Sleep stages (stacked hours), the need line and bed/wake bars: per night, or weekly means on long ranges
    weekly = len(days) > WEEKLY_AFTER

    def per(kind: str, nd: int = 2) -> list[list]:
        if not weekly:
            return col(kind, nd)
        acc: dict[date, list[float]] = {}
        for d in days:
            if kind in rows[d]:
                acc.setdefault(d - timedelta(days=d.weekday()), []).append(rows[d][kind])
        return [[w.isoformat(), round(fmean(v), nd)] for w, v in sorted(acc.items())]

    stages = {k: per(k) for k in ("deep_sleep_h", "rem_sleep_h", "light_sleep_h", "awake_h")}
    beds = dict(per("bedtime_h"))
    bed = [[d, b, round(w + 24 - b, 2)] for d, w in per("wake_h") if (b := beds.get(d)) is not None
           and 0 < w + 24 - b < 16]

    # Weekly HR zones (minutes) from the wearable's activities
    weeks: dict[date, list[float]] = {}
    for d in days:
        wk = d - timedelta(days=d.weekday())
        acc = weeks.setdefault(wk, [0.0] * 5)
        for z in range(5):
            acc[z] += rows[d].get(f"hr_zone{z + 1}_min", 0)
    zone_weeks = sorted(weeks)
    zones = {"weeks": [w.isoformat() for w in zone_weeks],
             "z": [[round(weeks[w][z]) for w in zone_weeks] for z in range(5)]}
    if not any(any(z) for z in zones["z"]):
        zones = None

    weekday = []
    for wd in range(7):
        ds = [d for d in days if d.weekday() == wd]
        weekday.append({"rec": _r(_mean([rows[d]["recovery_pct"] for d in ds if "recovery_pct" in rows[d]])),
                        "sleep": _r(_mean([rows[d]["sleep_hours"] for d in ds if "sleep_hours" in rows[d]]), 2)})

    scatter = [[rows[d]["sleep_performance_pct"], rows[d]["recovery_pct"], d.isoformat()]
               for d in days if "sleep_performance_pct" in rows[d] and "recovery_pct" in rows[d]]

    months: dict[str, dict[str, list[float]]] = {}
    for d in days:
        m = months.setdefault(d.strftime("%Y-%m"), {"rec": [], "hrv": [], "rhr": [], "sleep": []})
        for key, kind in (("rec", "recovery_pct"), ("hrv", "hrv_ms"), ("rhr", "resting_hr"), ("sleep", "sleep_hours")):
            if kind in rows[d]:
                m[key].append(rows[d][kind])
    monthly = {"months": list(months),
               **{k: [_r(_mean(v[k]), 2 if k == "sleep" else 1) for v in months.values()] for k in
                  ("rec", "hrv", "rhr", "sleep")}}

    flags = readiness.illness_flags(every, days)
    year = [[d.isoformat(), every[d]["recovery_pct"]] for d in sorted(every)
            if "recovery_pct" in every[d] and (today - d).days < 365]

    def delta(kind: str) -> dict:
        now, base = _window(every, kind, today, 0, 7), _window(every, kind, today, 7, BASE_DAYS)
        pct = (now / base - 1) * 100 if now is not None and base else None
        return {"now": _r(now), "base": _r(base), "pct": _r(pct, 0)}

    # Last night against the night before (WHOOP's sleep day view), stages as a donut
    nights = [d for d in sorted(every) if "sleep_hours" in every[d]]
    last_night = None
    if nights:
        ln, prev = every[nights[-1]], every[nights[-2]] if len(nights) > 1 else {}
        stats = []
        for kind, label, unit, nd, up_good in LAST_NIGHT:
            if kind in ln:
                pv = prev.get(kind)
                stats.append({"kind": kind, "label": label, "unit": unit, "now": _r(ln[kind], nd),
                              "prev": _r(pv, nd) if pv is not None else None,
                              "trend": None if pv is None or abs(ln[kind] - pv) < 1e-9 else
                              ("good" if (ln[kind] > pv) == up_good else "bad"),
                              "up": pv is not None and ln[kind] > pv})
        last_night = {"day": nights[-1], "stats": stats,
                      "stages": [[_(lbl), round(ln.get(k, 0), 2)] for k, lbl in
                                 (("deep_sleep_h", "Profundo"), ("rem_sleep_h", "REM"), ("light_sleep_h", "Ligero"),
                                  ("awake_h", "Despierto"))],
                      "in_bed": _r(ln.get("in_bed_h"), 2), "performance": _r(ln.get("sleep_performance_pct"), 0)}

    # How your nights spread by length, and how you woke up after each kind
    buckets = []
    for lo, hi, label in SLEEP_BUCKETS:
        ds = [d for d in days if "sleep_hours" in rows[d] and lo <= rows[d]["sleep_hours"] < hi]
        buckets.append({"label": label, "n": len(ds),
                        "rec": _r(_mean([rows[d]["recovery_pct"] for d in ds if "recovery_pct" in rows[d]]), 0),
                        "perf": _r(_mean([rows[d]["sleep_performance_pct"] for d in ds
                                          if "sleep_performance_pct" in rows[d]]), 0)})

    # Strain and energy split by the wearable's activities
    acts = activities.by_day(db, src, start)
    act_strain = [[d.isoformat(), round(acts[d]["top_strain"], 1), activities.sport_label(acts[d]["top_sport"])]
                  for d in days if d in acts and acts[d]["top_strain"] is not None]
    kcal_act = [[d.isoformat(), round(acts.get(d, {}).get("kcal", 0))] for d in days if "day_kcal" in rows[d]]
    kcal_rest = [[d.isoformat(), round(max(rows[d]["day_kcal"] - acts.get(d, {}).get("kcal", 0), 0))]
                 for d in days if "day_kcal" in rows[d]]

    last = every[max(every)]
    tiles = {"rec": delta("recovery_pct"), "hrv": delta("hrv_ms"), "rhr": delta("resting_hr"),
             "sleep": delta("sleep_hours"), "need": _r(_window(every, "sleep_need_h", today, 0, 7), 2),
             "debt": _r(last.get("sleep_debt_h"), 2), "day": max(every).isoformat()}

    return {
        "active": True, "empty": False, "source": src, "first": first, "start": start, "days": len(days),
        "tiles": tiles, "last_night": last_night, "buckets": buckets,
        "flags": [{"day": d, "signals": [readiness._signal_label(k) for k in ks]}
                  for d, ks in sorted(flags.items(), reverse=True)],
        "charts": {
            "dates": iso,
            "rec": [[d.isoformat(), rows[d]["recovery_pct"], readiness.band(rows[d]["recovery_pct"])]
                    for d in days if "recovery_pct" in rows[d]],
            "rec7": _rolling(rows, "recovery_pct", days, 7),
            "trained": sorted(d.isoformat() for d in trained if d in rows and "recovery_pct" in rows[d]),
            "trained_rec": [[d.isoformat(), rows[d]["recovery_pct"]] for d in sorted(trained)
                            if d in rows and "recovery_pct" in rows[d]],
            "sick": [[d.isoformat(), rows[d].get("recovery_pct", 0)] for d in sorted(flags)],
            "hrv": col("hrv_ms"), "hrv7": _rolling(rows, "hrv_ms", days, 7), "hrv_band": hrv_band,
            "rhr": col("resting_hr", 0), "rhr7": _rolling(rows, "resting_hr", days, 7),
            "stages": stages, "need": per("sleep_need_h"), "weekly": weekly, "sleep7": _rolling(rows, "sleep_hours", days, 7),
            "bed": bed, "strain": col("strain"), "strain7": _rolling(rows, "strain", days, 7),
            "kcal": col("day_kcal", 0), "kcal7": _rolling(rows, "day_kcal", days, 7),
            "act_strain": act_strain, "kcal_act": kcal_act, "kcal_rest": kcal_rest,
            "last_stages": last_night["stages"] if last_night else [], "buckets": buckets,
            "zones": zones, "weekday": weekday, "scatter": scatter, "monthly": monthly, "year": year,
        },
    }
