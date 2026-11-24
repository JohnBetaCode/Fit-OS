"""Assisted training: build today's workout from what you ask for, then adjust it before and during it.

- Focus: the program's next day, or muscle groups ("chest + shoulders"). Each group becomes slots (the first
  one per muscle asks for a compound lift, the rest for isolation) ranked by the planner (`rank_for_slot`:
  injuries, dislikes, history, variety, external load).
- Place and missing equipment: the place's tags minus what isn't there today ("no barbell").
- Mode: `smart` takes the planner's first choice; `random` draws from its top few, so two days with the same
  focus look different.
- Then: swap (alternatives that fit today's equipment), another one at random, add (search or a random one for a
  group), remove and reorder. Before the start these edit the proposed plan; during it they work on the live
  session (an item with sets logged is never removed, only skipped).

Loads always come from the usual progression on your history (`planner._target`).
"""

from __future__ import annotations

import random
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from ..core import prefs
from ..core.models import EquipmentProfile, Exercise, PlannedItem, PlannedSession, WorkoutSession
from ..i18n import _
from . import service as tr
from . import week as wk
from .planner import (
    ALL_TAGS,
    _equipment_profile,
    _history_ids,
    _recent_ids,
    _recently_planned,
    _target,
    rank_for_slot,
)
from .substitution import active_injured_joints, eligible, rank_alternatives

# Focus chips → primary muscles (catalog `primary_muscle` keys), in the order they are filled
GROUPS: dict[str, list[str]] = {
    "chest": ["chest"],
    "shoulders": ["delts"],
    "back": ["lats", "upper_back"],
    "biceps": ["biceps"],
    "triceps": ["triceps"],
    "legs": ["quads", "hamstrings", "glutes", "calves"],
    "glutes": ["glutes", "hamstrings"],
    "abs": ["abs"],
    "full": ["quads", "chest", "lats", "hamstrings", "delts", "upper_back"],
}
GROUP_ES = {
    "chest": "Pecho", "shoulders": "Hombro", "back": "Espalda", "biceps": "Bíceps", "triceps": "Tríceps",
    "legs": "Pierna", "glutes": "Glúteo", "abs": "Abdomen", "full": "Cuerpo completo",
}
# What can be missing today, in words people use (equipment_tag / requires keys)
MISSING_ES = {
    "barbell": "Barra", "dumbbell": "Mancuernas", "cable": "Poleas", "machine": "Máquinas", "smith": "Smith",
    "bench_flat": "Banco", "rack": "Rack", "pullup_bar": "Barra de dominadas", "kettlebell": "Kettlebell",
}
# Equipment people say they'd rather use today (equipment_tag keys)
PREFER_ES = {"dumbbell": "Mancuernas", "machine": "Máquinas", "cable": "Poleas", "barbell": "Barra",
             "bodyweight": "Peso corporal"}
MAX_SAME_TAG = 2  # after this many exercises with the same equipment, others go first (variety)
COMPOUND_REPS, ISOLATION_REPS = (6, 10), (10, 15)
RANDOM_POOL = 5


def group_labels() -> dict[str, str]:
    return {k: _(v) for k, v in GROUP_ES.items()}


def prefer_labels() -> dict[str, str]:
    return {k: _(v) for k, v in PREFER_ES.items()}


def missing_labels() -> dict[str, str]:
    return {k: _(v) for k, v in MISSING_ES.items()}


@dataclass
class Setup:
    """What the person asked for (remembered as the next day's default)."""

    on: date
    equipment_profile_id: int | None = None
    focus: str = "program"  # program | groups
    groups: list[str] = field(default_factory=list)
    count: int = 6
    missing: list[str] = field(default_factory=list)
    prefer: list[str] = field(default_factory=list)
    mode: str = "smart"  # smart | random

    def as_pref(self) -> dict:
        return {"equipment_profile_id": self.equipment_profile_id, "focus": self.focus, "groups": self.groups,
                "count": self.count, "missing": self.missing, "prefer": self.prefer, "mode": self.mode}


def last_setup(db: Session, on: date) -> Setup:
    saved = prefs.get(db, "assist_last") or {}
    s = Setup(on=on)
    for k in ("equipment_profile_id", "focus", "groups", "count", "missing", "prefer", "mode"):
        if k in saved:
            setattr(s, k, saved[k])
    return s


def today_prefer(db: Session, on: date) -> set[str]:
    """Equipment preferred for that day's plan (empty = no preference)."""
    saved = prefs.get(db, "assist_last") or {}
    return set(saved.get("prefer") or []) if saved.get("date") == on.isoformat() else set()


def today_missing(db: Session, on: date) -> set[str]:
    """Equipment marked as missing for that day (kept while the day's plan lasts)."""
    saved = prefs.get(db, "assist_last") or {}
    return set(saved.get("missing") or []) if saved.get("date") == on.isoformat() else set()


def _tags(ep: EquipmentProfile | None, missing: set[str]) -> set[str]:
    return (set(ep.tags or []) if ep else set(ALL_TAGS)) - missing


def _slots(groups: list[str], count: int) -> list[dict]:
    """Round-robin over the groups, cycling each group's muscles; the first slot of a muscle is compound."""
    seqs = [list(GROUPS[g]) for g in groups if g in GROUPS]
    if not seqs:
        return []
    out, seen, i = [], set(), 0
    while len(out) < count and i < count * 4:
        seq = seqs[i % len(seqs)]
        muscle = seq[(i // len(seqs)) % len(seq)]
        compound = muscle not in seen
        seen.add(muscle)
        lo, hi = COMPOUND_REPS if compound else ISOLATION_REPS
        out.append({"slot": "assist", "muscle": muscle, "mechanic": "compound" if compound else "isolation",
                    "sets": 3, "rep_min": lo, "rep_max": hi, "target_rir": 2})
        i += 1
    return out


def _pick(ranked: list[Exercise], slot: dict, mode: str, rng: random.Random,
          used_tags: Counter | None = None, prefer: set[str] = frozenset()) -> Exercise | None:
    """The planner's order, nudged: the slot's mechanic first, preferred equipment first, and equipment already
    used `MAX_SAME_TAG` times last (so a day isn't all machines). `random` draws from the top few."""
    if not ranked:
        return None
    used_tags = used_tags or Counter()
    want = slot.get("mechanic")
    fitting = [e for e in ranked if e.mechanic == want] or ranked
    fitting = sorted(fitting, key=lambda e: (bool(prefer) and e.equipment_tag not in prefer,
                                             used_tags[e.equipment_tag] >= MAX_SAME_TAG))
    if mode == "random":
        return rng.choice(fitting[:RANDOM_POOL])
    return fitting[0]


def _context(db: Session, on: date):
    prof = tr.get_profile(db)
    return {"history_ids": _history_ids(db), "recent_ids": _recent_ids(db, on),
            "injured": active_injured_joints(prof.injuries), "disliked": set(prof.disliked_exercise_ids or []),
            "recently_planned": _recently_planned(db, on)}


def build(db: Session, setup: Setup, rng: random.Random | None = None) -> PlannedSession:
    """Today's (or `setup.on`'s) plan from the setup; replaces a plan that hasn't started."""
    rng = rng or random.Random()
    prof = tr.get_profile(db)
    ep = _equipment_profile(db, setup.on, setup.equipment_profile_id, prof)
    missing = set(setup.missing)
    tags = _tags(ep, missing)
    prefs.set_(db, "assist_last", {**setup.as_pref(), "date": setup.on.isoformat()})
    if setup.focus == "program":
        plan = tr.get_or_build_plan(db, setup.on, ep.id if ep else None, replace=True)
        prefer = set(setup.prefer)
        for item in list(plan.items):  # what the program picked but isn't there today, or isn't preferred
            if prefer and eligible(item.exercise, tags) and item.exercise.equipment_tag not in prefer:
                alt = _random_for(db, plan, item.slot or {}, tags, "smart", rng, exclude={item.exercise_id})
                if alt is not None and alt.equipment_tag in prefer:
                    wk.swap_item(db, item, alt.id)
                continue
            if not eligible(item.exercise, tags):
                alt = _random_for(db, plan, item.slot or {}, tags, "smart", rng, exclude={item.exercise_id})
                if alt is not None:
                    wk.swap_item(db, item, alt.id)
                else:
                    wk.remove_item(db, item)
        return plan
    groups = [g for g in setup.groups if g in GROUPS]
    if not groups:
        raise ValueError(_("Elige al menos un grupo muscular."))
    count = max(2, min(12, int(setup.count)))
    ctx = _context(db, setup.on)
    used: set[str] = set()
    used_tags: Counter = Counter()
    prefer = set(setup.prefer)
    items = []
    for slot in _slots(groups, count):
        ranked = rank_for_slot(db, slot, tags, used=used, **ctx)
        ranked = [e for e in ranked if e.id not in ctx["disliked"] and not (set(e.stress_joints or []) & ctx["injured"])]
        ex = _pick(ranked, slot, setup.mode, rng, used_tags, prefer)
        if ex is None:
            continue
        used.add(ex.id)
        used_tags[ex.equipment_tag] += 1
        items.append({"exercise": ex.id, "sets": slot["sets"], "reps": [slot["rep_min"], slot["rep_max"]],
                      "rir": slot["target_rir"]})
    if not items:
        raise ValueError(_("No encontré ejercicios para eso con el equipo de hoy."))
    focus = " + ".join(group_labels()[g] for g in groups)
    return tr.put_custom_plan(db, setup.on, focus, items, equipment_profile_id=ep.id if ep else None,
                              rationale=_("Armado en Entrenar ({mode}).", mode=_("al azar") if setup.mode == "random" else _("sugerido")))


def empty_plan(db: Session, setup: Setup) -> PlannedSession:
    """"I'll choose": an empty plan for the day (replacing one that hasn't started) to fill by search."""
    from sqlalchemy import select  # noqa: PLC0415

    prof = tr.get_profile(db)
    ep = _equipment_profile(db, setup.on, setup.equipment_profile_id, prof)
    prefs.set_(db, "assist_last", {**setup.as_pref(), "date": setup.on.isoformat()})
    current = tr.plan_for(db, setup.on)
    if current is not None and current.status in ("active", "completed"):
        raise ValueError(_("El plan de ese día ya está {status}", status=current.status))
    for old in db.scalars(select(PlannedSession).where(PlannedSession.date == setup.on,
                                                      PlannedSession.status.in_(("proposed", "accepted", "skipped")))):
        old.status = "replaced"
    groups = [g for g in setup.groups if g in GROUPS] if setup.focus == "groups" else []
    plan = PlannedSession(date=setup.on, focus_label=" + ".join(group_labels()[g] for g in groups) or _("Mi entreno"),
                          equipment_profile_id=ep.id if ep else None, status="proposed", source="custom",
                          rationale=_("Armado en Entrenar (a mano)."))
    db.add(plan)
    db.flush()
    return plan


def _plan_tags(db: Session, plan: PlannedSession) -> set[str]:
    ep = db.get(EquipmentProfile, plan.equipment_profile_id) if plan.equipment_profile_id else None
    return _tags(ep, today_missing(db, plan.date))


def _random_for(db: Session, plan: PlannedSession, slot: dict, tags: set[str], mode: str, rng: random.Random,
                exclude: set[str] = frozenset()) -> Exercise | None:
    ctx = _context(db, plan.date)
    used = {i.exercise_id for i in plan.items} | set(exclude)
    ranked = rank_for_slot(db, {k: slot.get(k) for k in ("pattern", "muscle") if slot.get(k)}, tags, used=used, **ctx)
    ranked = [e for e in ranked if e.id not in ctx["disliked"] and not (set(e.stress_joints or []) & ctx["injured"])]
    used_tags = Counter(i.exercise.equipment_tag for i in plan.items if i.exercise_id not in exclude)
    return _pick(ranked, slot, mode, rng, used_tags, today_prefer(db, plan.date))


def options(db: Session, item: PlannedItem, limit: int = 8) -> list[Exercise]:
    """Alternatives for an item that fit today's equipment (place minus what's missing)."""
    plan = item.session
    original = db.get(Exercise, item.swapped_from_id or item.exercise_id) or item.exercise
    ws = tr.active_session(db)
    busy = set((ws.wa_context or {}).get("busy_stations") or []) if ws and ws.planned_session_id == plan.id else set()
    ctx = _context(db, plan.date)
    alts = rank_alternatives(
        db, original, _plan_tags(db, plan), busy_stations=busy,
        exclude_ids={i.exercise_id for i in plan.items} | ctx["disliked"], injured_joints=ctx["injured"],
        recent_ids=ctx["recent_ids"], history_ids=ctx["history_ids"], limit=limit * 4,
    )
    return _diverse([e for e, _score in alts], limit, today_prefer(db, plan.date))


def _diverse(ranked: list[Exercise], limit: int, prefer: set[str] = frozenset()) -> list[Exercise]:
    """Best of each kind of equipment first (preferred kinds before the rest), then the remaining by score:
    a list of swaps shows dumbbells and machines, not eight cable variations."""
    firsts: dict[str, Exercise] = {}
    for e in ranked:
        firsts.setdefault(e.equipment_tag, e)
    head = sorted(firsts.values(), key=lambda e: (bool(prefer) and e.equipment_tag not in prefer, ranked.index(e)))
    rest = [e for e in ranked if e not in head]
    return (head + rest)[:limit]


def _live(db: Session, plan: PlannedSession) -> WorkoutSession | None:
    ws = tr.active_session(db)
    return ws if ws and ws.planned_session_id == plan.id else None


def swap(db: Session, item: PlannedItem, exercise_id: str, reason: str = "preference") -> PlannedItem:
    ex = db.get(Exercise, exercise_id)
    if ex is None:
        raise LookupError(exercise_id)
    ws = _live(db, item.session)
    if ws is None:
        return wk.swap_item(db, item, ex.id)
    if tr.item_sets(ws, item):
        raise ValueError(_("Ese ejercicio ya tiene series: agrega otro en vez de cambiarlo."))
    return tr.swap_item(db, ws, item, ex, reason)


def reroll(db: Session, item: PlannedItem, rng: random.Random | None = None) -> PlannedItem:
    """Another exercise for the same muscle/pattern, at random among the good ones for today's equipment."""
    plan = item.session
    ex = _random_for(db, plan, item.slot or {"muscle": item.exercise.primary_muscle}, _plan_tags(db, plan), "random",
                     rng or random.Random(), exclude={item.exercise_id, item.swapped_from_id or ""})
    if ex is None:
        raise ValueError(_("No encontré otro ejercicio para eso con el equipo de hoy."))
    return swap(db, item, ex.id, "random")


def add(db: Session, plan: PlannedSession, exercise_id: str | None = None, group: str | None = None,
        rng: random.Random | None = None) -> PlannedItem:
    """Add an exercise (by id, or a random one for a group) to the plan, before or during the workout."""
    if exercise_id is None:
        muscles = GROUPS.get(group or "")
        if not muscles:
            raise ValueError(_("Elige un grupo muscular."))
        rng = rng or random.Random()
        slot = {"muscle": rng.choice(muscles), "mechanic": "isolation"}
        ex = _random_for(db, plan, slot, _plan_tags(db, plan), "random", rng)
        if ex is None:
            raise ValueError(_("No encontré ejercicios para eso con el equipo de hoy."))
        exercise_id = ex.id
    if plan.status in wk.EDITABLE:
        return wk.add_item(db, plan, exercise_id)
    if _live(db, plan) is None:
        raise ValueError(_("Ese plan ya empezó o terminó: no se puede editar"))
    ex = db.get(Exercise, exercise_id)
    if ex is None:
        raise LookupError(exercise_id)
    lo, hi = COMPOUND_REPS if ex.mechanic == "compound" else ISOLATION_REPS
    slot = {"slot": "added", "pattern": ex.movement_pattern, "muscle": ex.primary_muscle, "sets": 3,
            "rep_min": lo, "rep_max": hi, "target_rir": 2}
    ep = db.get(EquipmentProfile, plan.equipment_profile_id) if plan.equipment_profile_id else None
    t = _target(db, ex, slot, ep, (ep.loads or {}) if ep else {})
    item = PlannedItem(order_idx=len(plan.items), slot=slot, exercise_id=ex.id, sets=3, rep_min=t.rep_min,
                       rep_max=t.rep_max, target_rir=2, target_weight_kg=t.weight_kg, target_note=t.note or None)
    plan.items.append(item)
    db.flush()
    return item


def remove(db: Session, item: PlannedItem) -> None:
    """Before the start: out of the plan. During it: skipped (or refused if it already has sets)."""
    plan = item.session
    ws = _live(db, plan)
    if ws is None:
        wk.remove_item(db, item)
        return
    if tr.item_sets(ws, item):
        raise ValueError(_("Ese ejercicio ya tiene series."))
    was_current = tr.current_item(db, ws) is not None and tr.current_item(db, ws).id == item.id
    item.status = "skipped"
    if was_current:
        tr.advance(db, ws, "skipped")


def move(db: Session, item: PlannedItem, delta: int) -> None:
    if item.session.status in wk.EDITABLE:
        wk.move_item(db, item, delta)
        return
    plan = item.session
    items = list(plan.items)
    i = items.index(item)
    j = max(0, min(len(items) - 1, i + delta))
    if i != j:
        items[i], items[j] = items[j], items[i]
        for k, it in enumerate(items):
            it.order_idx = k
        plan.items.sort(key=lambda it: it.order_idx)


def focus_on(db: Session, ws: WorkoutSession, item: PlannedItem) -> None:
    """Make `item` the current exercise of the live session (you can do them in any order)."""
    cur = tr.current_item(db, ws)
    if cur is not None and cur.id != item.id and cur.status == "active":
        cur.status = "pending"
    if item.status in ("pending", "active"):
        item.status = "active"
    ws.wa_context = {**(ws.wa_context or {}), "current_item_id": item.id}


# ── Logging helpers for the web ──────────────────────────────────────────────────────────────────────────
CARDIO_FIELDS = ("level", "speed_kmh", "incline_pct", "steps", "distance_km")


def log_cardio(db: Session, ws: WorkoutSession, item: PlannedItem, minutes: float, extra: dict) -> None:
    """A timed item (cardio): duration plus machine details (level, speed, incline, steps, distance)."""
    from ..wa.parser import SetEntry  # noqa: PLC0415

    focus_on(db, ws, item)
    [st] = tr.log_sets(db, ws, item, [SetEntry(reps=None, weight_kg=None, duration_s=int(minutes * 60))],
                       raw_text="web")
    clean = {k: v for k, v in extra.items() if k in CARDIO_FIELDS and v is not None}
    st.extra = clean or None


def complete_as_planned(db: Session, plan: PlannedSession, item: PlannedItem) -> WorkoutSession:
    """✓ from Today: an item with no sets gets its planned sets logged with their targets (starting the workout
    if needed; each set can be corrected afterwards in Workouts). An item that already has sets is just closed
    as it is: nothing is invented for sets that weren't done."""
    from ..wa.parser import SetEntry  # noqa: PLC0415

    ws = tr.active_session(db)
    if ws is None or ws.planned_session_id != plan.id:
        if plan.status not in wk.EDITABLE and plan.status != "active":
            raise ValueError(_("Ese plan ya terminó."))
        ws = tr.start_session(db, plan, source="web")
        if ws.planned_session_id != plan.id:
            raise ValueError(_("Hay otro entreno en curso: termínalo primero."))
    done = len(tr.item_sets(ws, item))
    dur = (item.slot or {}).get("duration_min")
    focus_on(db, ws, item)
    if dur:
        if not done:
            minutes = dur[-1] if isinstance(dur, list | tuple) else dur
            tr.log_sets(db, ws, item, [SetEntry(reps=None, weight_kg=None, duration_s=int(float(minutes) * 60))],
                        raw_text="plan")
    elif not done:
        reps = item.rep_min  # the bottom of the range: claims no more than the plan asked
        entries = [SetEntry(reps=reps, weight_kg=item.target_weight_kg, rir=item.target_rir)
                   for _ in range(item.sets)]
        if entries:
            tr.log_sets(db, ws, item, entries, raw_text="plan")
    item.status = "done"
    nxt = tr.next_pending_item(plan)
    ws.wa_context = {**(ws.wa_context or {}), "current_item_id": nxt.id if nxt else None}
    if nxt:
        nxt.status = "active"
    return ws


def finish_item(db: Session, ws: WorkoutSession, item: PlannedItem) -> None:
    """"Done with this one" mid-workout, with however many sets it got (fewer or more than planned)."""
    if ws.planned_session_id != item.planned_session_id:
        raise ValueError(_("No hay un entreno en curso."))
    if not tr.item_sets(ws, item):
        raise ValueError(_("Anota al menos una serie, o quítalo."))
    was_current = (ws.wa_context or {}).get("current_item_id") == item.id
    item.status = "done"
    if was_current:
        tr.advance(db, ws)


# "Sueño 3/5 · Energía 3/5." (or the English one) at the start of a saved note, once or repeated
_NOTE_PREFIX = re.compile(r"^(?:(?:(?:Sueño|Energía|Sleep|Energy) [1-5]/5(?: · )?)+\.?\s*)+")


def save_note(ws: WorkoutSession, note: str, sleep: int | None, energy: int | None) -> None:
    """How the day went: free text plus sleep and energy (1–5), kept with the workout."""
    raw = (note or "").strip()
    head = _NOTE_PREFIX.match(raw)
    if head:  # not picked again: keep what the saved note said
        found = dict(re.findall(r"(Sueño|Energía|Sleep|Energy) ([1-5])/5", head.group(0)))
        sleep = sleep or int(found.get("Sueño") or found.get("Sleep") or 0) or None
        energy = energy or int(found.get("Energía") or found.get("Energy") or 0) or None
    parts = []
    if sleep:
        parts.append(_("Sueño {n}/5", n=sleep))
    if energy:
        parts.append(_("Energía {n}/5", n=energy))
    text = " · ".join(parts)
    note = raw[head.end():].strip() if head else raw  # the form shows the saved note: don't stack it
    ws.notes = (f"{text}. {note}" if text and note else text or note)[:1000] or None
