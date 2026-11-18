"""Textos que Fit-OS manda al grupo. Formato de WhatsApp: *negrita*, _cursiva_.

Every text goes through `_()` (English in `i18n/en_chat.py`); muscle and equipment labels come from
`catalog.service` in the current language."""

from __future__ import annotations

from ..catalog.service import MUSCLE_ES, equipment_label, muscle_label
from ..core.models import Exercise, PlannedItem, PlannedSession, WorkoutSet
from ..core.timeutil import fmt_date
from ..i18n import _, lang

__all__ = ["MUSCLE_ES"]  # re-exported for existing importers; labels live in the catalog


def fmt_w(w: float | None) -> str:
    """22.5 → '22,5' in Spanish, '22.5' in English; 40.0 → '40'."""
    if w is None:
        return "?"
    txt = f"{w:g}"
    return txt if lang() == "en" else txt.replace(".", ",")


def weight_text(weight: float | None, ex: Exercise) -> str:
    if ex.load_kind == "bodyweight":
        return _("peso corporal")
    if ex.load_kind == "duration":
        return ""
    if weight is None:
        return _("calibrar peso")
    if ex.load_kind == "per_hand":
        return _("{w} kg por mancuerna", w=fmt_w(weight))
    return f"{fmt_w(weight)} kg"


def rep_range(item: PlannedItem) -> str:
    """`8–10`, or `20–25 min` for a timed item (cardio) whose slot carries `duration_min: [lo, hi]`."""
    dur = (item.slot or {}).get("duration_min")
    if dur:
        lo, hi = (dur, dur) if isinstance(dur, int | float) else (dur[0], dur[-1])
        return f"{lo:g} min" if lo == hi else f"{lo:g}–{hi:g} min"
    return f"{item.rep_min}" if item.rep_min == item.rep_max else f"{item.rep_min}–{item.rep_max}"


def is_timed(item: PlannedItem) -> bool:
    return bool((item.slot or {}).get("duration_min")) or (item.exercise is not None
                                                          and item.exercise.load_kind == "duration")


def rest_text(seconds: int) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def equipment_line(ex: Exercise) -> str:
    parts = [muscle_label(ex.primary_muscle) if ex.primary_muscle else ""]
    eq = equipment_label(ex.equipment_tag) if ex.equipment_tag else ""
    reqs = [equipment_label(r) for r in ex.requires or []]
    parts.append(" + ".join([eq, *reqs]) if eq else " + ".join(reqs))
    return " · ".join(p for p in parts if p)


def plan_message(plan: PlannedSession, place: str, other: str, minutes: int, header: str | None = None) -> str:
    lines = [_("*{header} {focus}* en {place} · {date}", header=header or _("Hoy toca"), focus=_(plan.focus_label),
               place=place, date=fmt_date(plan.date)),
             _("{n} ejercicios · ~{minutes} min", n=len(plan.items), minutes=minutes), ""]
    for n, it in enumerate(plan.items, 1):
        ex = it.exercise
        w = weight_text(it.target_weight_kg, ex)
        lines.append(f"{n}. {ex.display_name} — {it.sets} × {rep_range(it)}{(' · ' + w) if w else ''}")
    if plan.rationale:
        lines += ["", f"_{plan.rationale}_"]
    lines += ["", _("*1* Empezar · *2* Cambiar a {other} · *3* Otro enfoque · *4* Hoy descanso", other=other)]
    return "\n".join(lines)


def warmup_line(item: PlannedItem, ramp: list[tuple[int, float | None]]) -> str:
    if not ramp:
        return ""
    if all(kg is None for _r, kg in ramp):
        return _("🔥 Calentamiento: {n} series ligeras antes del primer set, de {reps} reps (≈45 % y 65 % del peso "
                 "de trabajo)", n=len(ramp), reps=_(" y ").join(str(r) for r, _kg in ramp))
    each = _(" c/u") if item.exercise.load_kind == "per_hand" else ""
    steps = " · ".join(f"{fmt_w(kg)} kg{each} × {r}" for r, kg in ramp)
    return _("🔥 Calentamiento: {steps}", steps=steps)


def exercise_card(item: PlannedItem, idx: int, total: int, last: str | None, rest_s: int, done_sets: int = 0,
                  swipe: bool = False, warmup: list[tuple[int, float | None]] | None = None) -> str:
    ex = item.exercise
    w = weight_text(item.target_weight_kg, ex)
    lines = [f"*{idx}/{total} · {ex.display_name}*  ({equipment_line(ex)})"]
    tgt = _("Objetivo: {sets} × {reps}", sets=item.sets, reps=rep_range(item))
    if w:
        tgt += _(" con {w}", w=w)
    if is_timed(item):
        tgt += _(" · ritmo en el que puedas hablar")
    else:
        tgt += _(" · RIR {rir} · descanso {rest}", rir=item.target_rir, rest=rest_text(rest_s))
    lines.append(tgt)
    if item.target_note:
        lines.append(f"_{item.target_note}_")
    if warmup and not done_sets:
        lines.append(warmup_line(item, warmup) + _(" (anótalos con *c 10x20* si quieres)"))
    if last:
        lines.append(_("Última vez: {last}", last=last))
    if done_sets:
        lines.append(_("Llevas {done} de {sets} sets", done=done_sets, sets=item.sets))
    if swipe:
        lines.append(_("↩️ Desliza este mensaje y responde: {example} · otra · salta",
                       example=_("*25 min*") if is_timed(item) else _("*reps x kg* (10x22)")))
    elif is_timed(item):
        lines.append(_("Responde: *25 min* · ocupada · otra · salta · fin"))
    else:
        lines.append(_("Responde: *reps x kg* (10x22) · ocupada · otra · salta · video · fin"))
    return "\n".join(lines)


def describe_set(s: WorkoutSet, ex: Exercise) -> str:
    if s.duration_s and not s.reps:
        m, sec = divmod(s.duration_s, 60)
        if m >= 2:
            return f"{m} min" if not sec else f"{m}:{sec:02d} min"
        return f"{s.duration_s} s"
    parts = [f"{s.reps} reps"]
    if ex.load_kind == "bodyweight" and not s.weight_kg:
        parts.append(_("peso corporal"))
    elif s.weight_kg is not None:
        parts.append(f"{fmt_w(s.weight_kg)} kg" + (_(" c/u") if ex.load_kind == "per_hand" else ""))
    txt = " × ".join(parts)
    if s.rir is not None:
        txt += f", RIR {fmt_w(s.rir)}"
    return txt


PR_ES = {"e1rm": "1RM estimado", "weight": "peso máximo", "reps_at_weight": "reps a ese peso"}


def pr_line(sets: list[WorkoutSet]) -> str | None:
    flags: list[str] = []
    best = None
    for s in sets:
        for f in s.pr_flags or []:
            if f not in flags:
                flags.append(f)
        if s.e1rm_kg and (best is None or s.e1rm_kg > best):
            best = s.e1rm_kg
    if not flags:
        return None
    txt = _("🏆 *Récord*: {flags}", flags=", ".join(_(PR_ES[f]) if f in PR_ES else f for f in flags))
    if "e1rm" in flags and best:
        txt += f" (e1RM {fmt_w(round(best, 1))} kg)"
    return txt
