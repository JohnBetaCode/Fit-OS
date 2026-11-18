"""Progresión (design §7.5, research §4). Funciones puras: reciben historial y cargas, devuelven objetivos.

`next_target` devuelve un `PlannedTarget` (subclase de `wa.parser.Target`) con `note` en el idioma actual,
`load_ceiling` y `action` (calibrate | bodyweight | duration | hold | increase | regress | extend | ceiling)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from ..i18n import _
from ..wa.parser import Target, fmt_num

UPPER_MUSCLES = {"chest", "lats", "upper_back", "traps", "delts", "biceps", "triceps", "forearms"}
LIST_TAGS = {"dumbbell": "dumbbell_kg", "kettlebell": "kettlebell_kg"}
BAR_TAGS = {"barbell", "ez_bar", "trap_bar", "smith"}
MACHINE_TAGS = {"machine", "assisted_machine", "sled"}
CABLE_TAGS = {"cable", "rope"}
NO_LOAD_KINDS = {"bodyweight", "duration"}
CALIBRATION_NOTE = "Calibración: elige un peso con el que hagas ~10 dejando 3 en reserva"  # translated with _()
CEILING_EXTRA_REPS = 5
EPS = 1e-6


@dataclass
class SetPerf:
    reps: int | None
    weight_kg: float | None
    rir: float | None = None
    duration_s: int | None = None


@dataclass
class SessionPerf:
    date: date
    sets: list[SetPerf] = field(default_factory=list)
    equipment_profile_id: int | None = None


@dataclass
class PlannedTarget(Target):
    note: str = ""
    load_ceiling: bool = False
    action: str = "hold"


# ── e1RM ────────────────────────────────────────────────────────────────────


def e1rm(weight_kg: float | None, reps: int | None, rir: float | None = None) -> float | None:
    """Epley `w·(1 + r/30)` con r = reps + RIR; None si r > 12, sin reps o sin carga."""
    if not weight_kg or weight_kg <= 0 or not reps or reps <= 0:
        return None
    r = reps + (rir or 0)
    if r > 12:
        return None
    return round(weight_kg * (1 + r / 30), 1)


# ── Cargas disponibles ──────────────────────────────────────────────────────


def _available(tag: str, load_kind: str, loads: dict) -> tuple[list[float] | None, float]:
    """(lista de cargas, paso). Si hay lista manda la lista; si no, múltiplos del paso."""
    loads = loads or {}
    if tag in LIST_TAGS:
        lst = loads.get(LIST_TAGS[tag]) or loads.get("dumbbell_kg")
        if lst:
            return sorted({float(x) for x in lst}), 0.0
        return None, 2.0
    if tag in BAR_TAGS:
        return None, float(loads.get("barbell_step_kg") or 2.5)
    if tag in MACHINE_TAGS:
        return None, float(loads.get("machine_step_kg") or 5)
    if tag in CABLE_TAGS:
        return None, float(loads.get("cable_step_kg") or 2.5)
    plates = loads.get("plate_kg")
    if load_kind == "bodyweight_plus" and plates:
        return None, float(min(plates))
    return None, float(loads.get("default_step_kg") or 2.5)


def round_to_available(
    weight: float | None, exercise_load_kind: str | None, equipment_tag: str | None, loads: dict,
    direction: str = "nearest",
) -> float | None:
    """Ajusta a una carga real del perfil: mancuernas a los pares disponibles, barra al total con el paso,
    máquinas/poleas al paso. Peso corporal y duración → None. Empates de `nearest` → hacia abajo."""
    if weight is None or (exercise_load_kind or "total") in NO_LOAD_KINDS:
        return None
    lst, step = _available(equipment_tag or "", exercise_load_kind or "total", loads)
    if lst:
        if direction == "up":
            return next((x for x in lst if x >= weight - EPS), lst[-1])
        if direction == "down":
            return next((x for x in reversed(lst) if x <= weight + EPS), lst[0])
        return min(lst, key=lambda x: (abs(x - weight), x))
    q = weight / step
    n = {"up": math.ceil(q - EPS), "down": math.floor(q + EPS)}.get(direction)
    if n is None:
        n = math.floor(q + 0.5 - EPS)
    return round(max(n, 0) * step, 2)


def _below(weight: float, tag: str, load_kind: str, loads: dict) -> float:
    lst, step = _available(tag, load_kind, loads)
    if lst:
        return next((x for x in reversed(lst) if x < weight - EPS), lst[0])
    return round(max(math.ceil(weight / step - EPS) - 1, 0) * step, 2)


def next_step(weight: float | None, equipment_tag: str | None, load_kind: str | None, loads: dict,
              upper_body: bool) -> float | None:
    """Siguiente carga: +2,5 kg tren superior / +5 kg inferior en barra (y lastre); siguiente par en
    mancuernas; siguiente paso en máquinas y poleas. Si no hay más carga, devuelve la misma (techo)."""
    if weight is None or (load_kind or "total") in NO_LOAD_KINDS:
        return None
    tag, lk = equipment_tag or "", load_kind or "total"
    lst, step = _available(tag, lk, loads)
    if lst:
        return next((x for x in lst if x > weight + EPS), weight)
    if tag in MACHINE_TAGS or tag in CABLE_TAGS:
        return round(weight + step, 2)
    inc = 2.5 if upper_body else 5.0
    return round_to_available(weight + inc, lk, tag, loads, "up")


# ── Objetivo de la próxima sesión ───────────────────────────────────────────


def _work(sess: SessionPerf) -> tuple[float | None, list[SetPerf]]:
    """Carga de trabajo (la mayor) y los sets hechos con ella."""
    sets = [s for s in sess.sets if s.reps]
    weights = [s.weight_kg for s in sets if s.weight_kg is not None]
    if not weights:
        return None, sets
    w = max(weights)
    return w, [s for s in sets if s.weight_kg is not None and abs(s.weight_kg - w) < EPS]


def _missed(sess: SessionPerf, rep_min: int) -> bool:
    _, sets = _work(sess)
    return bool(sets) and sum(1 for s in sets if s.reps < rep_min) * 2 > len(sets)


def _reps_txt(sets: list[SetPerf]) -> str:
    return ", ".join(str(s.reps) for s in sets)


def next_target(
    history: list[SessionPerf], rep_min: int, rep_max: int, target_rir: int | None,
    exercise_meta: dict, loads: dict,
) -> PlannedTarget:
    """Doble progresión + autorregulación por RIR + retroceso + techo de carga. `history`: sesiones del mismo
    ejercicio, la más reciente primero (idealmente con el mismo perfil de equipo)."""
    lk = exercise_meta.get("load_kind") or "total"
    tag = exercise_meta.get("equipment_tag") or ""
    upper = exercise_meta.get("primary_muscle") in UPPER_MUSCLES
    rir_t = 2 if target_rir is None else target_rir

    def out(weight, note, action, lo=rep_min, hi=rep_max, ceiling=False) -> PlannedTarget:
        return PlannedTarget(lo, hi, weight, target_rir, lk, note=note, load_ceiling=ceiling, action=action)

    if lk == "duration":
        durs = [s.duration_s for sess in history[:1] for s in sess.sets if s.duration_s]
        if durs:
            return out(None, _("Última vez: {s} s; intenta sumar 5–10 s", s=max(durs)), "duration")
        return out(None, _("Series por tiempo: aguanta con buena técnica dejando 2 en reserva"), "duration")

    sessions = [s for s in history if any(x.reps for x in s.sets)]
    if not sessions:
        if lk == "bodyweight":
            return out(None, _("Peso corporal: {lo}–{hi} reps dejando {rir} en reserva", lo=rep_min, hi=rep_max,
                               rir=rir_t), "bodyweight")
        return out(None, _(CALIBRATION_NOTE), "calibrate")

    w, sets = _work(sessions[0])
    last = _reps_txt(sets)
    assisted = lk == "assisted"

    # Retroceso: dos sesiones seguidas con la mayoría de sets bajo rep_min.
    if len(sessions) >= 2 and _missed(sessions[0], rep_min) and _missed(sessions[1], rep_min):
        if w is None:
            return out(None, _("Dos sesiones bajo {n} reps: usa una variante más fácil", n=rep_min), "regress")
        if assisted:
            nw = next_step(w, tag, "total", loads, upper)
        else:
            nw = round_to_available(w * 0.925, lk, tag, loads, "nearest")
            if nw is None or nw >= w - EPS:
                nw = _below(w, tag, lk, loads)
        return out(nw, _("Retroceso: {w} kg tras dos sesiones bajo {n} reps", w=fmt_num(nw), n=rep_min), "regress")

    rir_ok = all(s.rir is None or s.rir >= rir_t for s in sets)
    all_top = bool(sets) and all(s.reps >= rep_max for s in sets) and rir_ok
    easy = bool(sets) and all(s.rir is not None and s.rir >= rir_t + 2 and s.reps >= rep_min for s in sets)

    def ceiling(weight) -> PlannedTarget:
        hi = rep_max + CEILING_EXTRA_REPS
        if all(s.reps >= hi for s in sets) and rir_ok:
            note = _("Techo de carga: ya haces {n} reps; conviene una variante más exigente (unilateral o pausa)", n=hi)
            return out(weight, note, "ceiling", hi=hi, ceiling=True)
        return out(weight, _("Tope de carga disponible: extendemos el rango a {lo}–{hi} reps", lo=rep_min, hi=hi),
                   "extend", hi=hi)

    if all_top or easy:
        why = (_("llegaste a {n} en todos los sets", n=rep_max) if all_top
               else _("te sobró reserva (RIR ≥ {rir})", rir=rir_t + 2))
        if w is None:
            if lk == "bodyweight_plus":
                nw = round_to_available(2.5 if upper else 5.0, lk, tag, loads, "up")
                return out(nw, _("Agrega {w} kg de lastre: {why}", w=fmt_num(nw), why=why), "increase")
            return ceiling(None)
        if assisted:
            nw = _below(w, tag, "total", loads)
            if nw < w - EPS:
                return out(nw, _("Menos asistencia: {w} kg; {why}", w=fmt_num(nw), why=why), "increase")
            return ceiling(w)
        nw = next_step(w, tag, lk, loads, upper)
        if nw is None or nw <= w + EPS:
            return ceiling(w)
        return out(nw, _("Subimos a {w} kg: {why}", w=fmt_num(nw), why=why), "increase")

    hold = round_to_available(w, lk, tag, loads, "nearest") if w is not None else None
    load = _(" con {w} kg", w=fmt_num(hold)) if hold is not None else ""
    return out(hold, _("Misma carga; suma 1 rep en los sets bajo {n} (última vez: {last}{load})", n=rep_max, last=last,
                       load=load), "hold")


# ── PRs ─────────────────────────────────────────────────────────────────────


def detect_prs(new_sets: list[SetPerf], prior_best: dict | None) -> list[str]:
    """Banderas entre `e1rm`, `weight`, `reps_at_weight` contra lo mejor previo
    (`{e1rm, weight, reps_at_weight: {peso: reps}}`). Sin registro previo de un tipo no hay PR de ese tipo
    (la primera vez no se celebra). Peso None cuenta como 0 en `reps_at_weight`."""
    prior = prior_best or {}
    best_e1rm, best_w = prior.get("e1rm"), prior.get("weight")
    raw = prior.get("reps_at_weight") or {}
    raw_reps = {round(float(k), 2): int(v) for k, v in raw.items()}
    flags: list[str] = []
    for s in new_sets:
        if not s.reps:
            continue
        est = e1rm(s.weight_kg, s.reps, s.rir)
        if est is not None and best_e1rm is not None and est > best_e1rm + EPS and "e1rm" not in flags:
            flags.append("e1rm")
        if s.weight_kg and best_w is not None and s.weight_kg > best_w + EPS and "weight" not in flags:
            flags.append("weight")
        key = round(float(s.weight_kg or 0), 2)
        if key in raw_reps and s.reps > raw_reps[key] and "reps_at_weight" not in flags:
            flags.append("reps_at_weight")
    order = ["e1rm", "weight", "reps_at_weight"]
    return [f for f in order if f in flags]
