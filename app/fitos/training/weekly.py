"""The Sunday summary: what the week was, against the previous one, and what goes up next week."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..body import service as body
from ..core import prefs
from ..core.models import EquipmentProfile, Exercise, PlannedItem, WorkoutSession, WorkoutSet
from . import metrics
from .planner import _meta, history_for
from .progression import next_target


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _week_sets(db: Session, start: date) -> list[WorkoutSet]:
    return [s for s, _d in metrics._sets_between(db, start, start + timedelta(days=7)) if not s.is_warmup]


def _weight_change(db: Session, end: date, kind: str = "weight_kg") -> dict | None:
    """Latest value up to `end` and the change against the latest one at least 7 days before it."""
    rows = [m for m in body.metric_series(db, kind) if m.measured_on <= end]
    if not rows:
        return None
    last = rows[-1]
    prev = next((m for m in reversed(rows) if m.measured_on <= last.measured_on - timedelta(days=7)), None)
    return {"kg": last.value, "on": last.measured_on,
            "delta": round(last.value - prev.value, 1) if prev else None}


def _ups(db: Session, sets: list[WorkoutSet]) -> list[dict]:
    """Exercises of the week whose next target is a heavier load."""
    out = []
    for ex_id in dict.fromkeys(s.exercise_id for s in sets if s.weight_kg):
        ex = db.get(Exercise, ex_id)
        item = db.scalar(select(PlannedItem).join(WorkoutSet, WorkoutSet.planned_item_id == PlannedItem.id)
                         .where(WorkoutSet.exercise_id == ex_id).order_by(WorkoutSet.id.desc()).limit(1))
        lo, hi, rir = (item.rep_min, item.rep_max, item.target_rir) if item else (8, 12, 2)
        last_ws = db.scalar(select(WorkoutSession).join(WorkoutSet).where(WorkoutSet.exercise_id == ex_id)
                            .order_by(WorkoutSession.date.desc()).limit(1))
        ep = db.get(EquipmentProfile, last_ws.equipment_profile_id) if last_ws and last_ws.equipment_profile_id else None
        hist = history_for(db, ex_id, ep.id if ep else None)
        if not hist:
            continue
        last_w = max((s.weight_kg or 0) for s in hist[0].sets)
        t = next_target(hist, lo, hi, rir, _meta(ex), (ep.loads or {}) if ep else {})
        if t.weight_kg and t.weight_kg > last_w:
            out.append({"exercise": ex.display_name, "from": last_w, "to": t.weight_kg,
                        "per_hand": ex.load_kind == "per_hand"})
    return out


def week_report(db: Session, start: date) -> dict:
    """Numbers for the week [start, start + 7 d) and the one before."""
    sets, prev = _week_sets(db, start), _week_sets(db, start - timedelta(days=7))
    sessions = db.scalars(select(WorkoutSession).where(WorkoutSession.date >= start,
                                                       WorkoutSession.date < start + timedelta(days=7))
                          .order_by(WorkoutSession.date)).all()
    sessions = [ws for ws in sessions if any(not s.is_warmup for s in ws.sets)]
    lo, hi = int(prefs.get(db, "weekly_sets_target_min")), int(prefs.get(db, "weekly_sets_target_max"))
    by_muscle = metrics.weekly_hard_sets_by_muscle(db, start)
    prs = []
    for s in sets:
        if s.pr_flags and s.exercise and s.exercise.display_name not in prs:
            prs.append(s.exercise.display_name)
    return {
        "start": start, "end": start + timedelta(days=6),
        "sessions": [(ws.date, ws.planned.focus_label if ws.planned else None) for ws in sessions],
        "target": body.get_profile(db).days_per_week,
        "sets": len(sets), "prev_sets": len(prev),
        "tonnage": metrics.tonnage(sets), "prev_tonnage": metrics.tonnage(prev),
        "minutes": sum(round((s.duration_s or 0) / 60) for s in sets if not s.reps),
        "prs": prs,
        "low": [m for m, v in by_muscle.items() if 0 < v < lo],
        "high": [m for m, v in by_muscle.items() if v > hi],
        "weight": _weight_change(db, start + timedelta(days=6)),
        "waist": _weight_change(db, start + timedelta(days=6), "waist_cm"),
        "ups": _ups(db, sets),
        "days": {k: v for k, v in _day_counts(db, start).items() if v},
    }


def _day_counts(db: Session, start: date) -> dict[str, int]:
    from ..body import days

    out: dict[str, int] = {}
    for row in days.between(db, start, start + timedelta(days=6)).values():
        out[row.kind] = out.get(row.kind, 0) + 1
    return out


def report_text(r: dict, portal: str = "") -> str:
    from ..catalog.service import muscle_label
    from ..core.timeutil import fmt_day_month
    from ..i18n import _
    from ..wa.cards import fmt_w

    def pct(now: float, before: float) -> str:
        if not before:
            return ""
        return f" ({(now - before) / before * 100:+.0f} %)"

    n = len(r["sessions"])
    lines = [_("📊 *Tu semana* · {start} – {end}", start=fmt_day_month(r["start"]), end=fmt_day_month(r["end"])),
             _("Entrenos: {n}/{target}", n=n, target=r["target"]) + (" ✅" if n >= r["target"] else "")]
    if r["sessions"]:
        lines.append("· " + ", ".join(_(f) if f else "—" for _d, f in r["sessions"]))
    lines.append(_("Sets: {sets}{d1} · {kg} kg movidos{d2}", sets=r["sets"], d1=pct(r["sets"], r["prev_sets"]),
                   kg=fmt_w(round(r["tonnage"])), d2=pct(r["tonnage"], r["prev_tonnage"])))
    if r["minutes"]:
        lines.append(_("Cardio: {m} min", m=r["minutes"]))
    if r.get("days"):
        from ..body.days import KIND_ICON, label

        lines.append(" · ".join(f"{KIND_ICON.get(k, '')} {label(k)}: {v}" for k, v in r["days"].items()))
    if r["prs"]:
        lines.append(_("🏆 Récords: {names}", names=", ".join(r["prs"][:6])))
    if r["low"]:
        lines.append(_("Poco volumen: {muscles}", muscles=", ".join(muscle_label(m) for m in r["low"][:6])))
    if r["high"]:
        lines.append(_("Mucho volumen: {muscles}", muscles=", ".join(muscle_label(m) for m in r["high"][:6])))
    w = r["weight"]
    if w:
        delta = "" if w["delta"] is None else f" ({'+' if w['delta'] > 0 else ''}{fmt_w(w['delta'])} kg)"
        lines.append(_("⚖️ Peso: {kg} kg{delta}", kg=fmt_w(w["kg"]), delta=delta))
    wa = r.get("waist")
    if wa and wa["on"] >= r["start"]:
        delta = "" if wa["delta"] is None else f" ({'+' if wa['delta'] > 0 else ''}{fmt_w(wa['delta'])} cm)"
        lines.append(_("📏 Cintura: {cm} cm{delta}", cm=fmt_w(wa["kg"]), delta=delta))
    if r["ups"]:
        lines += ["", _("⬆️ *Sube la próxima semana*")]
        for u in r["ups"][:8]:
            each = _(" c/u") if u["per_hand"] else ""
            lines.append(f"· {u['exercise']}: {fmt_w(u['from'])} → {fmt_w(u['to'])} kg{each}")
    if not n:
        lines.append(_("Semana sin entrenos. Escribe *hoy* el próximo día de entreno y arrancamos."))
    if portal:
        lines += ["", f"{portal.rstrip('/')}/progreso"]
    return "\n".join(lines)
