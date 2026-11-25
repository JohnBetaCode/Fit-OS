"""Conversación del grupo (design §7.2–7.3): plan del día, sesión en vivo, cambios, cierre.

El camino feliz del gimnasio es determinista y no llama al LLM; lo que no es un comando va al coach.

Commands are understood in Spanish and English whatever the language preference; replies follow `lang()`.
Collisions, decided on purpose:
- "done", "ready", "next" close the current exercise (like "listo"); the session ends with "finish", "end",
  "done for today", "all done", "that's it" (like "fin"). Before starting, "done/ready/next/yes/ok" start the plan.
- "change", "swap", "other", "another" ask for another exercise during a session; before starting, changing the
  place is "switch"/"change place" and the other day of the rotation is "other focus"/"other day".
- "skip" skips the current exercise; "skip today", "rest", "day off" (before starting) take a rest day.
- "no" + equipment ("no barbell") marks it missing; a bare "no" only answers the RPE question."""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..body import service as body
from ..catalog.service import equipment_label
from ..core import prefs
from ..core.db import session_scope
from ..core.models import EquipmentProfile, Exercise, PlannedItem, PlannedSession, WorkoutSession, WorkoutSet
from ..core.settings import get_settings
from ..core.timeutil import fmt_day_month, local_now, local_today, utcnow
from ..i18n import _, lang
from ..training import service as tr
from . import cards
from .outbox import enqueue_media, enqueue_text, gym_chat
from .parser import SetEntry, Target, parse_set_log
from .router import norm

log = logging.getLogger("fitos.wa.session")

# Commands (normalized: lowercase, no accents). Spanish first, then English; both work in either language.
START = {"1", "empezar", "empecemos", "vamos", "dale", "start", "arrancar", "listo empezar",
         "go", "lets go", "let's go", "begin", "start workout", "lets start", "let's start"}
SWITCH = {"2", "cambiar", "cambiar lugar",
          "switch", "change place", "change location", "switch place", "other place", "other gym"}
OTHER_FOCUS = {"3", "otro enfoque", "otro dia", "otra rutina",
               "other focus", "another focus", "different focus", "other day", "other workout", "another workout"}
REST_DAY = {"4", "descanso", "hoy descanso", "hoy no", "no puedo hoy",
            "rest", "rest day", "rest today", "day off", "not today", "skip today", "cant today", "can't today"}
PLAN_WORDS = {"hoy", "plan", "que toca", "que toca hoy", "rutina", "que me toca", "que me toca hoy",
              "today", "whats today", "what's today", "todays plan", "today's plan", "whats the plan",
              "what's the plan", "workout", "routine"}
BUSY = {"ocupada", "ocupado", "esta ocupada", "esta ocupado", "busy",
        "taken", "occupied", "in use", "its taken", "it's taken", "station busy"}
NEXT_ALT = {"otra", "cambia", "otro", "cambiar ejercicio", "otra opcion",
            "other", "another", "swap", "change", "another one", "different", "alternative", "other option"}
SKIP = {"salta", "saltar", "skip", "paso", "omitir", "skip it", "skip this", "pass"}
DONE_ITEM = {"listo", "siguiente", "next", "ya", "termine",
             "done", "ready", "next one", "next exercise", "move on", "im done", "i'm done"}
YES = {"si", "sí", "ok", "yes", "yep", "sure"}
VIDEO = {"video", "vid", "animacion", "gif", "clip", "demo", "show me"}
HOWTO = {"como", "como se hace", "tips", "pasos", "tecnica",
         "how", "how to", "how do i do it", "steps", "technique", "form", "cues"}
UNDO = {"deshacer", "borra", "borrar", "undo", "delete", "remove", "delete last", "undo last", "oops"}
EXTRA = {"+1", "extra", "otro set", "uno mas", "one more", "extra set", "another set", "one more set"}
FINISH = {"fin", "terminar", "terminamos", "acabe", "end", "cerrar", "debo terminar", "tengo que terminar",
          "me tengo que ir", "tengo que irme", "debo irme", "me voy", "ya me voy", "no alcanzo", "no me alcanza",
          "se me acabo el tiempo", "hasta aqui", "finish", "end session", "finish session", "done for today",
          "all done", "thats it", "that's it", "wrap up", "gotta go", "got to go", "i have to go", "have to go",
          "out of time", "no time", "i need to go"}
STATUS = {"estado", "status", "donde voy", "where am i", "progress", "where are we"}
NO_RPE = {"no", "nada", "omitir", "salta", "skip", "none", "nothing", "pass", "no thanks"}

RE_NO_TENGO = re.compile(r"^(?:no tengo una|no tengo|no hay una|no hay|sin|i don'?t have|don'?t have|dont have"
                         r"|there'?s no|there is no|without|no free|no)\s+(?:an?\s+|any\s+)?(.+)$")
RE_CORRIGE = re.compile(r"^(?:corrige|corregir|correccion|fix|correct|edit)\s+(.+)$")
RE_PLACE = re.compile(r"^(?:hoy en|entreno en|estoy en|today at|today in|training at|i'?m at|at)\s+(?:the\s+)?(.+)$")
RE_WARMUP = re.compile(r"^(?:c|cal|calent|calentamiento|w|wu|warm ?up|warmup)\s*:?\s+(.+)$")
RE_PESO = re.compile(r"^(?:peso|pese|me pese|weight|weigh|weighed|i weigh|body ?weight|bw)\s*:?\s*"
                     r"(\d{2,3}(?:[.,]\d+)?)\s*(kg|kgs|lb|lbs)?$")


# ── utilidades ──────────────────────────────────────────────────────────────


def _clip_path(ex: Exercise) -> Path | None:
    if not ex.media_mp4:
        return None
    p = get_settings().data_dir / "catalog" / "mp4" / ex.media_mp4
    return p if p.is_file() else None


def _rest_s(db: Session, ex: Exercise) -> int:
    key = "rest_seconds_compound" if ex.mechanic == "compound" else "rest_seconds_isolation"
    return int(prefs.get(db, key))


def _last_time(db: Session, ex: Exercise, exclude_session: int | None = None) -> str | None:
    q = (select(WorkoutSet).join(WorkoutSession).where(WorkoutSet.exercise_id == ex.id, WorkoutSet.is_warmup.is_(False))
         .order_by(WorkoutSession.date.desc(), WorkoutSet.id))
    if exclude_session:
        q = q.where(WorkoutSet.session_id != exclude_session)
    rows = db.scalars(q.limit(40)).all()
    if not rows:
        return None
    last_sid = rows[0].session_id
    sets = [s for s in rows if s.session_id == last_sid]
    reps = ", ".join(str(s.reps or s.duration_s or "?") for s in sets)
    weights = {s.weight_kg for s in sets if s.weight_kg}
    d = sets[0].session.date
    when = d.strftime("%d/%m") if lang() == "es" else fmt_day_month(d)
    if ex.load_kind == "bodyweight" or not weights:
        return f"{reps} ({when})"
    return _("{reps} con {w} kg ({when})", reps=reps, w=cards.fmt_w(max(weights)), when=when)


def _profiles(db: Session, plan: PlannedSession) -> tuple[EquipmentProfile | None, EquipmentProfile | None]:
    """The plan's equipment profile and the first other one (for "switch place")."""
    ep = db.get(EquipmentProfile, plan.equipment_profile_id) if plan.equipment_profile_id else None
    others = [e for e in body.equipment_profiles(db) if not ep or e.id != ep.id]
    return ep, (others[0] if others else None)


def _place(db: Session, plan: PlannedSession) -> tuple[str, str]:
    ep, other = _profiles(db, plan)
    return body.equipment_name(ep) if ep else "Gym", body.equipment_name(other) if other else _("otro lugar")


def _estimate_minutes(plan: PlannedSession) -> int:
    sets = sum(i.sets for i in plan.items)
    return max(20, int(round(sets * 2.6 / 5) * 5))


def _plan_text(db: Session, plan: PlannedSession, header: str | None = None) -> str:
    place, other = _place(db, plan)
    return cards.plan_message(plan, place, other, _estimate_minutes(plan), header)


def _all_cards(db: Session) -> bool:
    return prefs.get(db, "wa_cards") == "all"


def _warmup(db: Session, ws: WorkoutSession, item: PlannedItem) -> list[tuple[int, float | None]] | None:
    """Ramp for the first compound loaded lift of each primary muscle, in plan order."""
    from ..training.warmup import needs_warmup, warmup_sets

    if not prefs.get(db, "warmup_sets") or cards.is_timed(item):
        return None
    warmed = {i.exercise.primary_muscle or "" for i in ws.planned.items
              if i.order_idx < item.order_idx and i.exercise.mechanic == "compound"
              and i.exercise.load_kind in ("total", "per_hand")}
    ex = item.exercise
    if not needs_warmup(ex.mechanic, ex.load_kind, ex.primary_muscle, warmed):
        return None
    ep = db.get(EquipmentProfile, ws.equipment_profile_id) if ws.equipment_profile_id else None
    return warmup_sets(item.target_weight_kg, ex.load_kind, ex.equipment_tag, (ep.loads or {}) if ep else {})


def _schedule_rest(db: Session, ws: WorkoutSession, item: PlannedItem | None, rest_s: int) -> None:
    """One pending "rest is over" timer per session: a new one (or any new set) replaces the old."""
    from ..core.models import Timer

    for t in db.scalars(select(Timer).where(Timer.kind == "rest", Timer.status == "pending")):
        t.status = "cancelled"
    if (item is None or not prefs.get(db, "rest_alert") or prefs.get(db, "wa_mode") == "reports"
            or cards.is_timed(item) or rest_s <= 0):
        return
    db.add(Timer(kind="rest", due_at=utcnow() + timedelta(seconds=rest_s),
                 payload={"ws": ws.id, "item": item.id, "n_sets": len(ws.sets)}))


async def rest_over(payload: dict) -> None:
    """Timer handler: post "rest is over" if nothing was logged meanwhile and the session is still on."""
    with session_scope() as db:
        ws = db.get(WorkoutSession, payload.get("ws"))
        item = db.get(PlannedItem, payload.get("item"))
        if ws is None or item is None or ws.state != "active" or len(ws.sets) != payload.get("n_sets"):
            return
        if item.status not in ("pending", "active"):
            return
        n = len(tr.item_sets(ws, item)) + 1
        w = cards.weight_text(item.target_weight_kg, item.exercise)
        last = next((s for s in reversed(tr.item_sets(ws, item)) if s.weight_kg), None)
        if last is not None:
            w = cards.weight_text(last.weight_kg, item.exercise)
        enqueue_text(db, _("⏱️ Descanso listo. Set {n}/{sets} de *{name}*: {reps}", n=n, sets=max(item.sets, n),
                           name=item.exercise.display_name, reps=cards.rep_range(item))
                     + (f" × {w}" if w else ""), handler="rest", planned_item_id=item.id)


def _send_card(db: Session, ws: WorkoutSession, item: PlannedItem, force_clip: bool = False, prefix: str = "") -> None:
    plan = ws.planned
    idx = [i.id for i in plan.items].index(item.id) + 1
    ex = item.exercise
    text = cards.exercise_card(item, idx, len(plan.items), _last_time(db, ex, ws.id), _rest_s(db, ex),
                               len(tr.item_sets(ws, item)), swipe=_all_cards(db), warmup=_warmup(db, ws, item))
    if prefix:
        text = f"{prefix}\n\n{text}"
    policy = prefs.get(db, "clip_policy")
    first_time = db.scalar(select(WorkoutSet.id).where(WorkoutSet.exercise_id == ex.id).limit(1)) is None
    clip = _clip_path(ex)
    if clip and (force_clip or policy == "always" or (policy == "first_time" and first_time)):
        enqueue_media(db, "video", str(clip), text, handler="card", planned_item_id=item.id)
    else:
        enqueue_text(db, text, handler="card", planned_item_id=item.id)


def _send_start(db: Session, ws: WorkoutSession, item: PlannedItem) -> None:
    """First card, or (with `wa_cards = all`) a short intro plus one card per pending exercise."""
    if not _all_cards(db):
        _send_card(db, ws, item, prefix=_("💪 ¡Vamos!"))
        return
    pending = [i for i in ws.planned.items if i.status in ("pending", "active")]
    enqueue_text(db, _("💪 ¡Vamos! Te mando los {n} ejercicios, uno por mensaje. Desliza el de cada ejercicio y "
                       "responde ahí tus sets (*8x60 rir2*), en el orden que quieras. Si escribes sin deslizar, va al "
                       "ejercicio en curso: *{name}*.", n=len(pending), name=item.exercise.display_name),
                 handler="start")
    for it in pending:
        _send_card(db, ws, it)


def quoted_item(db: Session, msg_id: int) -> PlannedItem | None:
    """The plan item of the Fit-OS message this one replies to (WhatsApp swipe → reply), if any."""
    from ..core.models import WaMessage

    msg = db.get(WaMessage, msg_id)
    if msg is None or not msg.quoted_wa_id:
        return None
    iid = db.scalar(select(WaMessage.planned_item_id).where(WaMessage.wa_id == msg.quoted_wa_id,
                                                           WaMessage.direction == "out"))
    return db.get(PlannedItem, iid) if iid else None


def _focus(ws: WorkoutSession, item: PlannedItem) -> None:
    """Make `item` the current exercise (a reply to its card); the one it replaces goes back to the queue."""
    ctx = dict(ws.wa_context or {})
    cur_id = ctx.get("current_item_id")
    if cur_id == item.id:
        return
    for it in ws.planned.items:
        if it.id == cur_id and it.status == "active":
            it.status = "pending"
    item.status = "active"
    ctx["current_item_id"] = item.id
    ws.wa_context = ctx


def _summary_text(db: Session, ws: WorkoutSession) -> str:
    from ..training.metrics import session_summary

    s = session_summary(db, ws.id)
    mins = s.get("duration_min") or 0
    lines = [_("✅ *Sesión cerrada*: {mins} min · {sets} sets · {kg} kg movidos", mins=mins, sets=s.get("sets", 0),
               kg=cards.fmt_w(round(s.get("tonnage_kg", 0))))]
    prs = s.get("prs") or []
    if prs:
        names = ", ".join(list(dict.fromkeys(p["exercise"] for p in prs))[:5])
        n = len(set(p["exercise"] for p in prs))
        lines.append(_("🏆 {n} récord: {names}", n=n, names=names) if n == 1
                     else _("🏆 {n} récordes: {names}", n=n, names=names))
    left = [i.exercise.display_name for i in (ws.planned.items if ws.planned else []) if i.status == "skipped"]
    if left:
        lines.append(_("Quedó pendiente: {names}", names=", ".join(left[:6])))
    prog = tr.active_program(db)
    if prog and prog.days:
        nxt = prog.days[prog.rotation_idx % len(prog.days)]
        lines.append(_("Próxima: *{name}*", name=_(nxt.name)))
    if ws.session_rpe:
        lines.append(_("RPE de la sesión: {rpe}/10", rpe=ws.session_rpe))
    return "\n".join(lines)


PLAN_STATUS_ES = {"proposed": "propuesto", "accepted": "aceptado", "active": "en curso", "completed": "completado",
                  "skipped": "saltado", "replaced": "reemplazado"}


def _status(status: str) -> str:
    es = PLAN_STATUS_ES.get(status)
    return _(es) if es else status


def _target(item: PlannedItem) -> Target:
    return Target(item.rep_min, item.rep_max, item.target_weight_kg, item.target_rir, item.exercise.load_kind or "total")


# ── entrada ─────────────────────────────────────────────────────────────────


async def handle_text(msg_id: int, text: str) -> str:
    n = norm(text)
    coach_needed = False
    with session_scope() as db:
        body.ensure_defaults(db)
        ws = tr.active_session(db)
        target = quoted_item(db, msg_id) if msg_id else None
        if target is not None:
            if ws is None:  # replying to a card of today's plan before "1" starts the session
                plan = target.session
                if plan.date == local_today() and plan.status in ("proposed", "accepted"):
                    ws = tr.start_session(db, plan)
            if ws is not None and target.planned_session_id == ws.planned_session_id and ws.state == "active":
                _focus(ws, target)
            else:
                target = None
        if ws:
            ws.last_activity_at = utcnow()
            ctx = dict(ws.wa_context or {})
            ctx.pop("nudged", None)
            ws.wa_context = ctx
            handler = _in_session(db, ws, n, text, quoted=target is not None)
        else:
            handler = _idle(db, n, text)
        if handler == "coach":
            coach_needed = True
    if coach_needed:
        return await _coach(text)
    return handler


def options_text(db: Session) -> str:
    """What you can write right now (shown whenever a message isn't understood): it depends on whether a session
    is running, waiting for the RPE, or today's plan is pending, done or missing."""
    ws = tr.active_session(db)
    if ws and ws.state == "finishing":
        return _("Estoy esperando el RPE de la sesión: responde de *1* a *10*, o *no* para cerrar sin él.")
    if ws:
        item = tr.current_item(db, ws)
        cur = _("Ahora: *{name}*.", name=item.exercise.display_name) + " " if item else ""
        log = _("*25 min*") if item and cards.is_timed(item) else _("*8x60* o *8x60 rir2*")
        return cur + _("Puedes escribir:") + "\n" + "\n".join([
            _("• {log} — anotar un set (o desliza la tarjeta de otro ejercicio)", log=log),
            _("• *listo* — siguiente ejercicio · *salta* — saltarlo"),
            _("• *otra* / *ocupada* / *no tengo barra* — cambiar el ejercicio"),
            _("• *video* · *como* (técnica) · *+1* (un set más)"),
            _("• *borra* (último set) · *corrige 8x60* · *estado*"),
            _("• *debo terminar* — cerrar la sesión"),
        ])
    today = local_today()
    plan = tr.plan_for(db, today)
    if plan and plan.status in ("proposed", "accepted"):
        _place_now, other = _place(db, plan)
        head = _("Hoy toca *{focus}*. Puedes escribir:", focus=_(plan.focus_label))
        lines = [_("• *1* — empezar"), _("• *2* — cambiar a {other} · *3* — otro enfoque · *4* — hoy descanso",
                                           other=other), _("• *hoy* — ver el plan otra vez")]
    elif plan and plan.status == "completed":
        head, lines = _("Hoy ya entrenaste ✅. Puedes escribir:"), []
    else:
        head, lines = _("Puedes escribir:"), [_("• *hoy* — armar y ver el plan de hoy")]
    lines += [_("• *peso 80.5* · *cintura 84* — registrar medidas"),
              _("• *enfermo* · *de viaje* — marcar el día"),
              _("• *ayuda* — todos los comandos")]
    return head + "\n" + "\n".join(lines)


async def _coach(text: str) -> str:
    from ..coach.agent import coach_reply  # noqa: PLC0415

    reply = await coach_reply(text, channel="wa")
    with session_scope() as db:
        if reply:
            enqueue_text(db, reply, handler="coach")
            return "coach"
        enqueue_text(db, _("No entendí 🤔.") + " " + options_text(db), handler="unknown")
    return "unknown"


def _idle(db: Session, n: str, raw: str) -> str:
    today = local_today()
    m = RE_PESO.match(n)
    if m:
        v = float(m.group(1).replace(",", "."))
        if (m.group(2) or "").startswith("lb"):
            v = round(v * 0.45359237, 1)
        try:
            body.log_metric(db, "weight_kg", v)
        except ValueError:
            enqueue_text(db, _("Ese peso ({w} kg) no parece real 🤨", w=cards.fmt_w(v)), handler="weight")
            return "weight"
        enqueue_text(db, _("Anotado: {w} kg hoy ⚖️", w=cards.fmt_w(v)), handler="weight")
        return "weight"
    if n in STATUS:
        plan = tr.plan_for(db, today)
        enqueue_text(db, _("No hay sesión activa.") + (
            _(" Plan de hoy: {focus} ({status}).", focus=_(plan.focus_label), status=_status(plan.status)) if plan
            else _(" Escribe *hoy* para ver el plan.")), handler="status")
        return "status"
    plan = tr.plan_for(db, today)
    pending = plan is not None and plan.status in ("proposed", "accepted")
    if n in PLAN_WORDS:
        try:
            plan = tr.get_or_build_plan(db, today)
        except Exception as e:  # noqa: BLE001 — sin programa o sin catálogo: se explica
            log.warning("no pude armar el plan: %s", e)
            enqueue_text(db, _("No pude armar el plan: {error}", error=e), handler="plan")
            return "plan"
        if plan.status == "completed":
            enqueue_text(db, _("Hoy ya entrenaste ✅. Mañana seguimos."), handler="plan")
            return "plan"
        enqueue_text(db, _plan_text(db, plan), handler="plan")
        return "plan"
    if n in START or (pending and n in DONE_ITEM | YES):
        if not pending:
            try:
                plan = tr.get_or_build_plan(db, today)
            except Exception as e:  # noqa: BLE001
                enqueue_text(db, _("No pude armar el plan: {error}", error=e), handler="start")
                return "start"
        ws = tr.start_session(db, plan)
        item = tr.current_item(db, ws)
        if item is None:
            enqueue_text(db, _("El plan está vacío 🤷. Escribe *hoy* para regenerarlo."), handler="start")
            return "start"
        _send_start(db, ws, item)
        return "start"
    if pending and n in SWITCH:
        _cur, ep = _profiles(db, plan)
        new = tr.get_or_build_plan(db, today, ep.id if ep else None, replace=True)
        place = body.equipment_name(ep) if ep else _("otro lugar")
        enqueue_text(db, _plan_text(db, new, header=_("Cambio a {place}:", place=place)), handler="switch")
        return "switch"
    if pending and n in OTHER_FOCUS:
        prog = tr.active_program(db)
        if prog and prog.days:
            prog.rotation_idx = (prog.rotation_idx + 1) % len(prog.days)
        new = tr.get_or_build_plan(db, today, plan.equipment_profile_id, replace=True)
        enqueue_text(db, _plan_text(db, new, header=_("Otro enfoque:")), handler="other_focus")
        return "other_focus"
    if pending and n in REST_DAY:
        tr.skip_today(db, today)
        enqueue_text(db, _("Ok, hoy descanso 😴. La rotación no se pierde: mañana sigue donde iba."), handler="rest")
        return "rest"
    m = RE_PLACE.match(n)
    if m:
        ep = body.equipment_by_name(db, m.group(1))
        if ep:
            new = tr.get_or_build_plan(db, today, ep.id, replace=True)
            enqueue_text(db, _plan_text(db, new, header=_("Plan en {place}:", place=body.equipment_name(ep))), handler="switch")
            return "switch"
    return "coach"


def _in_session(db: Session, ws: WorkoutSession, n: str, raw: str, quoted: bool = False) -> str:
    item = tr.current_item(db, ws)
    ctx = dict(ws.wa_context or {})

    if ws.state == "finishing":
        m = re.match(r"^(\d{1,2})(?:\s*/\s*10)?$", n)
        if m and 1 <= int(m.group(1)) <= 10:
            tr.finish_session(db, ws, int(m.group(1)))
            db.flush()
            enqueue_text(db, _summary_text(db, ws), handler="finish")
            return "finish"
        if n in NO_RPE:
            tr.finish_session(db, ws, None)
            db.flush()
            enqueue_text(db, _summary_text(db, ws), handler="finish")
            return "finish"
        enqueue_text(db, _("¿Qué tan dura estuvo la sesión, de *1* a *10*?"), handler="rpe")
        return "rpe"

    if n in STATUS:
        plan = ws.planned
        done = sum(1 for i in plan.items if i.status == "done")
        cur = f"{item.exercise.display_name} ({len(tr.item_sets(ws, item))}/{item.sets} sets)" if item else "—"
        enqueue_text(db, _("Sesión {focus}: {done}/{total} ejercicios hechos · ahora: {cur} · {sets} sets en total",
                           focus=_(plan.focus_label), done=done, total=len(plan.items), cur=cur, sets=len(ws.sets)),
                     handler="status")
        return "status"
    if n in FINISH or item is None:
        ws.state = "finishing"
        enqueue_text(db, _("¿Qué tan dura estuvo la sesión, de *1* a *10*? (o *no*)"), handler="finish_ask")
        return "finish_ask"
    if n in UNDO:
        last = tr.undo_last_set(db, ws)
        enqueue_text(db, _("Borrado el último set ({reps}×{w})", reps=last.reps, w=cards.fmt_w(last.weight_kg)) if last
                     else _("No hay sets que borrar."), handler="undo")
        return "undo"
    m = RE_CORRIGE.match(n)
    if m:
        res = parse_set_log(m.group(1), _target(item))
        if res.ok and len(res.entries) == 1:
            st = tr.edit_last_set(db, ws, res.entries[0])
            enqueue_text(db, _("Corregido: {set}", set=cards.describe_set(st, db.get(Exercise, st.exercise_id))) if st
                         else _("No hay sets que corregir."), handler="edit")
        else:
            enqueue_text(db, _("No entendí la corrección. Ejemplo: *corrige 8x40*"), handler="edit")
        return "edit"
    if n in EXTRA:
        item.sets += 1
        enqueue_text(db, _("Va un set más: {sets} en total para {name}.", sets=item.sets,
                           name=item.exercise.display_name), handler="extra")
        return "extra"
    if n in VIDEO:
        clip = _clip_path(item.exercise)
        if clip:
            enqueue_media(db, "video", str(clip), item.exercise.display_name, handler="video")
        else:
            url = item.exercise.youtube_url or ""
            enqueue_text(db, _("No tengo el clip a mano. Mira la técnica aquí: {url}", url=url), handler="video")
        return "video"
    if n in HOWTO:
        ex = item.exercise
        steps = ex.instructions or []
        txt = "\n".join(f"{i}. {s}" for i, s in enumerate(steps[:6], 1)) or _("No tengo los pasos de este ejercicio.")
        enqueue_text(db, f"*{item.exercise.display_name}*\n{txt}", handler="howto")
        return "howto"
    if n in SKIP:
        nxt = tr.advance(db, ws, "skipped")
        return _after_advance(db, ws, nxt, _("Saltado {name}.", name=item.exercise.display_name))
    if n in DONE_ITEM:
        done = len(tr.item_sets(ws, item))
        nxt = tr.advance(db, ws, "done" if done else "skipped")
        return _after_advance(db, ws, nxt, _("{name}: {n} sets ✔️", name=item.exercise.display_name, n=done))

    # Cambios por equipo
    excluded = None
    reason = None
    if n in BUSY:
        station = item.exercise.station or f"ex:{item.exercise_id}"
        ctx["busy_stations"] = sorted(set(ctx.get("busy_stations") or []) | {station})
        reason = "busy"
    else:
        m = RE_NO_TENGO.match(n)
        if m:
            from ..training.substitution import word_to_tag

            tag = word_to_tag(m.group(1))
            if tag is None:
                return "coach"
            excluded = tag
            ctx["excluded_tags"] = sorted(set(ctx.get("excluded_tags") or []) | {tag})
            reason = "no_equipment"
    if reason or n in NEXT_ALT:
        ws.wa_context = ctx
        db.flush()
        cursor = dict(ctx.get("alt_cursor") or {})
        key = str(item.id)
        pos = cursor.get(key, 0) if n in NEXT_ALT else 0
        alts = tr.alternatives(db, ws, item, limit=8)
        if reason:  # descarta la estación ocupada / el equipo que falta también para el actual
            alts = [(e, s) for e, s in alts if e.id != item.exercise_id]
        if pos >= len(alts):
            enqueue_text(db, _("No me quedan alternativas para este. Escribe *salta* para seguir."), handler="no_alt")
            return "no_alt"
        new_ex, _score = alts[pos]
        cursor[key] = pos + 1
        ctx["alt_cursor"] = cursor
        ws.wa_context = ctx
        tr.swap_item(db, ws, item, new_ex, reason or "preference")
        if reason == "busy":
            why = _("estación ocupada")
        elif reason == "no_equipment":
            why = _("sin {equipment}", equipment=equipment_label(excluded).lower())
        else:
            why = _("otra opción")
        _send_card(db, ws, item, force_clip=_clip_path(new_ex) is not None and prefs.get(db, "clip_policy") != "on_demand",
                   prefix=_("🔄 Anotado: {why}. Cambio a *{name}*.", why=why, name=new_ex.display_name))
        return "swap"

    m = RE_WARMUP.match(n)
    if m and re.search(r"\d", m.group(1)):
        res = parse_set_log(m.group(1), _target(item))
        if res.ok and res.entries:
            sets = tr.log_sets(db, ws, item, res.entries, raw, warmup=True)
            enqueue_text(db, _("🔥 Calentamiento anotado: {sets} (no cuenta para récords ni volumen)",
                               sets="; ".join(cards.describe_set(s, item.exercise) for s in sets)),
                         handler="warmup", planned_item_id=item.id)
            return "warmup"

    # Registro de sets
    if re.search(r"\d", n):
        last_w = next((s.weight_kg for s in reversed(tr.item_sets(ws, item)) if s.weight_kg), None)
        res = parse_set_log(raw, _target(item), last_weight=last_w)
        if res.ok and res.entries:
            loaded = item.exercise.load_kind in ("total", "per_hand")
            if loaded and any(e.weight_kg is None and not e.duration_s for e in res.entries):
                reps = res.entries[0].reps
                enqueue_text(db, _("¿Con cuánto peso fueron esas {reps} reps? Escríbelo como *{example}*.", reps=reps,
                                   example=f"{reps}x{cards.fmt_w(22.5)}"), handler="ask_weight")
                return "ask_weight"
            return _log(db, ws, item, res.entries, raw, named=quoted or _all_cards(db))
        if res.ambiguous and res.entries:
            e = res.entries[0]
            enqueue_text(db, _("¿Fueron {reps} reps con {w} kg? Escríbelo como *reps x kg*.", reps=e.reps,
                               w=cards.fmt_w(e.weight_kg)), handler="ambiguous")
            return "ambiguous"
        enqueue_text(db, _("No entendí el set. Ejemplos: *10x40*, *8x22,5 rir2*, *12 bw*, *45s*.") + "\n\n"
                     + options_text(db), handler="parse_fail")
        return "parse_fail"
    return "coach"


def _log(db: Session, ws: WorkoutSession, item: PlannedItem, entries: list[SetEntry], raw: str,
         named: bool = False) -> str:
    ex = item.exercise
    new_sets = tr.log_sets(db, ws, item, entries, raw)
    done = tr.item_sets(ws, item)
    conf = "; ".join(_("Set {n}: {set}", n=s.set_idx, set=cards.describe_set(s, ex)) for s in new_sets)
    lines = [f"*{ex.display_name}* · {conf}" if named else conf]
    pr = cards.pr_line(new_sets)
    if pr:
        lines.append(pr)
    if len(done) >= item.sets:
        reps = ", ".join(str(s.reps) if s.reps else cards.describe_set(s, ex) for s in done)
        best = max((s.e1rm_kg or 0) for s in done)
        lines.append(_("*{name}* listo: {reps}", name=ex.display_name, reps=reps)
                     + (f" · e1RM {cards.fmt_w(round(best, 1))} kg" if best else ""))
        nxt = tr.advance(db, ws, "done")
        _schedule_rest(db, ws, nxt, _rest_s(db, ex))
        return _after_advance(db, ws, nxt, "\n".join(lines))
    # sugerencia intra-sesión: set muy fácil → subir
    last = new_sets[-1]
    hint = ""
    if last.rir is not None and item.target_rir is not None and last.rir >= item.target_rir + 3:
        hint = _(" Quedó fácil: sube un paso en el siguiente.")
    nxt_w = cards.weight_text(last.weight_kg or item.target_weight_kg, ex)
    lines.append(_("Siguiente: set {n}/{sets}, {reps}", n=len(done) + 1, sets=item.sets, reps=cards.rep_range(item))
                 + (f" × {nxt_w}" if nxt_w else "")
                 + _(". Descanso {rest}.", rest=cards.rest_text(_rest_s(db, ex))) + hint)
    enqueue_text(db, "\n".join(lines), handler="set", planned_item_id=item.id)
    _schedule_rest(db, ws, item, _rest_s(db, ex))
    return "set"


def _after_advance(db: Session, ws: WorkoutSession, nxt: PlannedItem | None, prefix: str) -> str:
    if nxt is None:
        ws.state = "finishing"
        enqueue_text(db, prefix + "\n\n" + _("¡Terminaste el plan! 🔥 ¿Qué tan dura estuvo la sesión, de *1* a *10*?"),
                     handler="finish_ask")
        return "finish_ask"
    if _all_cards(db):  # its card is already in the chat: just say which one is next
        idx = [i.id for i in ws.planned.items].index(nxt.id) + 1
        enqueue_text(db, prefix + "\n\n" + _("Sigue *{idx}/{total} · {name}*: {sets} × {reps}. Desliza su tarjeta o "
                                             "escribe aquí.", idx=idx, total=len(ws.planned.items),
                                             name=nxt.exercise.display_name, sets=nxt.sets,
                                             reps=cards.rep_range(nxt)),
                     handler="next", planned_item_id=nxt.id)
        return "next"
    _send_card(db, ws, nxt, prefix=prefix)
    return "next"


# ── jobs ────────────────────────────────────────────────────────────────────


async def check_timeouts() -> str | None:
    with session_scope() as db:
        ws = tr.active_session(db)
        if not ws:
            return None
        idle = (utcnow() - ws.last_activity_at).total_seconds() / 60
        ctx = dict(ws.wa_context or {})
        if idle >= int(prefs.get(db, "session_idle_close_min")):
            if not ws.sets:  # opened but nothing logged: not a workout, the plan stays available
                tr.discard_empty_session(db, ws)
                return "discarded"
            tr.finish_session(db, ws, None)
            db.flush()
            enqueue_text(db, _("Cerré la sesión por inactividad.") + "\n" + _summary_text(db, ws), handler="timeout")
            return "closed"
        if (idle >= int(prefs.get(db, "session_idle_nudge_min")) and not ctx.get("nudged")
                and prefs.get(db, "wa_mode") != "reports"):
            ctx["nudged"] = True
            ws.wa_context = ctx
            enqueue_text(db, _("¿Seguimos o cierro la sesión? (escribe *fin* para cerrar)"), handler="nudge")
            return "nudged"
    return None


async def send_morning_plan() -> str | None:
    with session_scope() as db:
        body.ensure_defaults(db)
        if not gym_chat():
            return _("sin grupo configurado")
        now = local_now()
        h, m = (prefs.get(db, "morning_hour") or "06:30").split(":")
        start = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        if now - start > timedelta(hours=3):
            return _("fuera de ventana (el proceso arrancó tarde)")
        today = now.date()
        if not tr.is_training_day(db, today):
            return _("día de descanso")
        from ..body import days

        marked = days.between(db, today, today).get(today)
        if marked:
            return _("día marcado: {kind}", kind=days.label(marked.kind))
        if tr.active_session(db):
            return _("ya hay sesión activa")
        existing = tr.plan_for(db, today)
        if existing and existing.status in ("active", "completed", "skipped"):
            return _("plan {status}", status=_status(existing.status))
        prof = body.get_profile(db)
        day_key = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][today.weekday()]
        ep_id = ((prof.schedule or {}).get(day_key) or {}).get("equipment_profile_id")
        plan = tr.get_or_build_plan(db, today, ep_id)
        plan_id = plan.id
    from ..coach.agent import adjust_plan  # noqa: PLC0415

    await adjust_plan(plan_id)
    with session_scope() as db:
        plan = db.get(PlannedSession, plan_id)
        from ..body import days

        back = days.recently(db, "sick", days=3)
        intro = ("🤒 " + _("Estuviste enfermo hace poco. ¿Cómo sigues? Si aún no estás bien escribe *enfermo* y lo "
                          "dejamos; si entrenas, baja ~10 % las cargas.") + "\n\n") if back else ""
        from ..body.composition import weigh_in_nudge

        nudge = weigh_in_nudge(db, today)
        enqueue_text(db, intro + "☀️ " + _plan_text(db, plan) + (f"\n\n{nudge}" if nudge else ""), handler="morning")
    return _("enviado")
