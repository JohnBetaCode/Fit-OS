"""Coach IA (design §8). Loop manual de tools con hilos solo-añadir: `system` y `tools` congelados; los mensajes
de un hilo se guardan tal como se enviaron/recibieron y se reenvían idénticos (preserved thinking + caché).
El contexto dinámico va como mensaje `role: system` después del mensaje del usuario, nunca en `system`."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select

from ..body import service as body
from ..core.db import session_scope
from ..core.models import ChatMessage, ChatThread, PlannedItem, PlannedSession
from ..core.settings import get_settings
from ..core.timeutil import fmt_date, local_today
from ..i18n import _, lang
from ..training import service as tr
from . import llm as llm_mod
from .prompts import ADJUST_PROMPTS, SYSTEM_PROMPTS
from .tools import HANDLERS, TOOLS, ToolError

log = logging.getLogger("fitos.coach")

MAX_ITER = 6
WA_MAX_CHARS = 1500


def _system_blocks() -> list[dict]:
    """The frozen prompt of the current language (tools are the same in both)."""
    return [{"type": "text", "text": SYSTEM_PROMPTS[lang()], "cache_control": {"type": "ephemeral"}}]


def _block_dict(b: Any) -> dict:
    if isinstance(b, dict):
        return b
    return b.model_dump(mode="json", exclude_none=True)


def _thread_for(db, channel: str, thread_id: int | None) -> ChatThread:
    if thread_id:
        t = db.get(ChatThread, thread_id)
        if t:
            return t
    if channel == "wa":
        t = db.scalar(select(ChatThread).where(ChatThread.channel == "wa", ChatThread.day == local_today(),
                                               ChatThread.closed_at.is_(None)).order_by(ChatThread.id.desc()).limit(1))
        if t:
            return t
    t = ChatThread(channel=channel, day=local_today(), title=None)
    db.add(t)
    db.flush()
    return t


def _append(db, thread: ChatThread, role: str, content: Any, visible: str | None = None) -> None:
    seq = (db.scalar(select(func.max(ChatMessage.seq)).where(ChatMessage.thread_id == thread.id)) or 0) + 1
    db.add(ChatMessage(thread_id=thread.id, seq=seq, role=role, content=content, visible_text=visible))
    db.flush()


def _history(db, thread: ChatThread) -> list[dict]:
    rows = db.scalars(select(ChatMessage).where(ChatMessage.thread_id == thread.id).order_by(ChatMessage.seq)).all()
    return [{"role": m.role, "content": m.content} for m in rows]


def _ficha(db) -> str:
    """Contexto del momento (va como mensaje system tras el mensaje del usuario)."""
    p = body.get_profile(db)
    w = body.latest_metric(db)
    plan = tr.plan_for(db, local_today())
    ws = tr.active_session(db)
    lines = [_("Fecha: {date} ({iso}).", date=fmt_date(local_today()), iso=local_today().isoformat()),
             _("Usuario: {sex}, {age} años, {height} cm, {kg} kg, nivel {level}, objetivo {goal}, {days} días/semana.",
               sex=p.sex, age=body.age(p), height=p.height_cm or "?", kg=w.value if w else "?", level=p.experience,
               goal=p.primary_goal, days=p.days_per_week)]
    if plan:
        lines.append(_("Plan de hoy: {focus} ({status}), {n} ejercicios.", focus=_(plan.focus_label),
                       status=plan.status, n=len(plan.items)))
    if ws:
        cur = tr.current_item(db, ws)
        cur_txt = f"{cur.exercise.display_name} (planned_item_id {cur.id})" if cur else _("ninguno")
        lines.append(_("Sesión activa ({state}); ejercicio actual: {cur}; {n} sets hechos.", state=ws.state,
                       cur=cur_txt, n=len(ws.sets)))
    else:
        lines.append(_("No hay sesión activa."))
    return _("Contexto actual (lo agrega el sistema, no el usuario):") + "\n" + "\n".join(lines)


def _run_tool(name: str, args: dict) -> tuple[str, bool]:
    fn = HANDLERS.get(name)
    if fn is None:
        return _("tool desconocida: {name}", name=name), True
    try:
        with session_scope() as db:
            out = fn(db, args or {})
        return json.dumps(out, ensure_ascii=False, default=str)[:20000], False
    except ToolError as e:
        return f"Error: {e}", True
    except Exception as e:  # noqa: BLE001
        log.exception("tool %s falló", name)
        return _("Error interno en {name}: {error}", name=name, error=type(e).__name__), True


async def coach_reply(text: str, channel: str = "wa", thread_id: int | None = None) -> str | None:
    """Un turno del coach. Devuelve el texto final o None si el LLM no está disponible (modo determinista)."""
    if not llm_mod.available():
        return None
    s = get_settings()
    client = llm_mod.get_llm()
    with session_scope() as db:
        thread = _thread_for(db, channel, thread_id)
        tid = thread.id
        _append(db, thread, "user", [{"type": "text", "text": text}], visible=text)
        _append(db, thread, "system", _ficha(db))
        messages = _history(db, thread)

    final_text = ""
    for i in range(MAX_ITER + 1):
        kw: dict[str, Any] = dict(model=s.llm_coach_model, system=_system_blocks(), tools=TOOLS, messages=messages,
                                  max_tokens=4000, effort="low")
        if i == MAX_ITER:
            kw["tool_choice"] = {"type": "none"}
        try:
            resp = await client.chat("coach_" + channel, **kw)
        except Exception:  # noqa: BLE001
            log.exception("llamada al coach falló")
            return _("Ahora mismo no puedo pensar (falló la llamada a Claude). Los comandos siguen funcionando.")
        if resp.stop_reason == "refusal":
            return _("Eso no lo puedo responder. Pregúntame sobre tu entrenamiento, tu plan o tus exámenes.")
        content = [_block_dict(b) for b in resp.content]
        with session_scope() as db:
            _append(db, db.get(ChatThread, tid), "assistant", content,
                    visible="".join(b.get("text", "") for b in content if b.get("type") == "text") or None)
        messages.append({"role": "assistant", "content": content})
        final_text = "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
        if resp.stop_reason == "max_tokens":
            return (final_text or _("Me extendí demasiado."))[:WA_MAX_CHARS]
        uses = [b for b in content if b.get("type") == "tool_use"]
        if resp.stop_reason != "tool_use" or not uses:
            break
        results = []
        for u in uses:
            out, is_err = _run_tool(u["name"], u.get("input") or {})
            r = {"type": "tool_result", "tool_use_id": u["id"], "content": out}
            if is_err:
                r["is_error"] = True
            results.append(r)
        with session_scope() as db:
            _append(db, db.get(ChatThread, tid), "user", results)
        messages.append({"role": "user", "content": results})
    if channel == "wa" and len(final_text) > WA_MAX_CHARS:
        final_text = final_text[:WA_MAX_CHARS].rsplit("\n", 1)[0] + "\n…"
    return final_text or None


# ── Ajuste del plan del día (una llamada, salida estructurada) ──────────────


class Swap(BaseModel):
    planned_item_id: int
    new_exercise_id: str
    reason: str


class PlanAdjust(BaseModel):
    rationale: str
    swaps: list[Swap]


ADJUST_PROMPT = ADJUST_PROMPTS["es"]  # kept for existing imports; `adjust_plan` uses the current language


def plan_context(db, plan: PlannedSession) -> tuple[str, dict[int, set[str]]]:
    """Texto de contexto para el ajuste + alternativas permitidas por ítem (ids)."""
    from datetime import timedelta  # noqa: PLC0415

    from ..coach.tools import get_recent_sessions  # noqa: PLC0415
    from ..training import metrics as m  # noqa: PLC0415

    p = body.get_profile(db)
    today = local_today()
    week = m.weekly_hard_sets_by_muscle(db, today - timedelta(days=today.weekday()))
    lines = [_ficha(db),
             _("Lesiones: {injuries}. Notas: {notes}", injuries=p.injuries or _("ninguna"),
               notes=f"{p.goal_notes or '-'} {p.coach_notes or ''}"),
             _("Sets duros por músculo esta semana: {sets}", sets=json.dumps({k: round(v, 1) for k, v in week.items()})),
             _("Últimos 14 días: {sessions}",
               sessions=json.dumps(get_recent_sessions(db, {"days": 14}), ensure_ascii=False)),
             _("Plan candidato: {focus}", focus=_(plan.focus_label))]
    allowed: dict[int, set[str]] = {}
    for it in plan.items:
        alts = tr.alternatives(db, None, it, limit=3)
        allowed[it.id] = {e.id for e, _ in alts}
        alt_txt = "; ".join(f"{e.id}={e.display_name}" for e, _ in alts)
        lines.append(f"- planned_item_id {it.id}: {it.exercise.display_name} {it.sets}x{it.rep_min}-{it.rep_max} "
                     f"@{it.target_weight_kg or '?'} kg | "
                     + _("alternativas: {alts}", alts=alt_txt or _("ninguna")))
    return "\n".join(lines), allowed


async def adjust_plan(plan_id: int, timeout_s: float = 45.0) -> None:
    """Pule el plan candidato con el LLM. Si no hay LLM, falla o tarda: el plan queda tal cual (siempre llega)."""
    if not llm_mod.available():
        return
    s = get_settings()
    with session_scope() as db:
        plan = db.get(PlannedSession, plan_id)
        if plan is None:
            return
        context, allowed = plan_context(db, plan)
    try:
        resp = await asyncio.wait_for(llm_mod.get_llm().parse(
            "plan_adjust", PlanAdjust, model=s.llm_coach_model, effort="medium", max_tokens=8000,
            messages=[{"role": "user", "content": ADJUST_PROMPTS[lang()].format(context=context)}],
        ), timeout=timeout_s)
    except Exception:  # noqa: BLE001
        log.warning("ajuste del plan con LLM falló; se envía el plan determinista", exc_info=True)
        return
    adj = getattr(resp, "parsed_output", None)
    if resp.stop_reason == "refusal" or adj is None:
        return
    with session_scope() as db:
        plan = db.get(PlannedSession, plan_id)
        plan.rationale = adj.rationale[:400]
        from ..core.models import Exercise  # noqa: PLC0415

        for sw in adj.swaps[:2]:
            item = db.get(PlannedItem, sw.planned_item_id)
            if item is None or item.planned_session_id != plan_id or sw.new_exercise_id not in allowed.get(item.id, ()):
                continue
            ex = db.get(Exercise, sw.new_exercise_id)
            if ex:
                tr.swap_item(db, None, item, ex, "coach", actor="llm")
                item.note = sw.reason[:200]
        plan.source = "llm"
