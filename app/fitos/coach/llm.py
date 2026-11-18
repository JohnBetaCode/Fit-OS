"""Cliente de Claude (real) y su doble (`FakeLLM`). Registra cada llamada en `llm_calls` con costo y caché.

Opus 5.5: el thinking no se desactiva (effort explícito), `tool_choice` forzado da 400, sin prefill; se
activa `fallbacks="default"` (beta `server-side-fallback-2026-07-01`) y siempre se revisa `stop_reason`."""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from datetime import datetime
from datetime import time as dtime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import func, select

from ..core import prefs
from ..core.db import session_scope
from ..core.models import LlmCall
from ..core.settings import get_settings
from ..core.timeutil import local_now, local_tz
from ..i18n import _

FALLBACK_BETA = "server-side-fallback-2026-07-01"

# USD por millón de tokens: (input, output, cache_read)
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}


def cost_usd(model: str, usage: Any) -> float:
    pin, pout, pcache = PRICES.get(model, PRICES["claude-opus-5-5"])
    inp = getattr(usage, "input_tokens", 0) or 0
    out = getattr(usage, "output_tokens", 0) or 0
    cr = getattr(usage, "cache_read_input_tokens", 0) or 0
    cw = getattr(usage, "cache_creation_input_tokens", 0) or 0
    return (inp * pin + out * pout + cr * pcache + cw * pin * 1.25) / 1e6


def spent_today_usd() -> float:
    start = datetime.combine(local_now().date(), dtime.min, tzinfo=local_tz())
    with session_scope() as db:
        return float(db.scalar(select(func.coalesce(func.sum(LlmCall.cost_usd), 0.0)).where(LlmCall.at >= start)) or 0)


def available() -> bool:
    s = get_settings()
    if s.llm_mode == "off":
        return False
    if s.llm_mode == "live" and not s.anthropic_api_key:
        return False
    with session_scope() as db:
        budget = float(prefs.get(db, "llm_daily_budget_usd") or 0)
    return spent_today_usd() < budget


def _record(purpose: str, model: str, resp: Any, started: float) -> None:
    usage = getattr(resp, "usage", None)
    with session_scope() as db:
        db.add(LlmCall(
            purpose=purpose, model=model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cost_usd=cost_usd(model, usage) if usage else 0.0,
            latency_ms=int((time.monotonic() - started) * 1000),
            stop_reason=getattr(resp, "stop_reason", None),
        ))


class LiveLLM:
    def __init__(self) -> None:
        import anthropic

        self._client = anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key, max_retries=2, timeout=90.0)

    async def chat(self, purpose: str, **kw) -> Any:
        """Turno con tools (beta para `fallbacks`). `kw` = model, system, tools, messages, effort, max_tokens."""
        model = kw.pop("model")
        effort = kw.pop("effort", "low")
        started = time.monotonic()
        extra = {"fallbacks": "default"} if model.startswith(("claude-opus", "claude-sonnet")) else {}
        betas = [FALLBACK_BETA] if extra else []
        resp = await self._client.beta.messages.create(
            model=model, output_config={"effort": effort}, betas=betas, cache_control={"type": "ephemeral"},
            **extra, **kw,
        )
        _record(purpose, model, resp, started)
        return resp

    async def parse(self, purpose: str, output_format: type, **kw) -> Any:
        model = kw.pop("model")
        effort = kw.pop("effort", "low")
        started = time.monotonic()
        resp = await self._client.messages.parse(model=model, output_config={"effort": effort},
                                                 output_format=output_format, **kw)
        _record(purpose, model, resp, started)
        return resp


@dataclass
class FakeLLM:
    """Doble para desarrollo y tests. `script` es una cola de respuestas (objetos tipo Message); vacía →
    responde un texto fijo sin tools."""

    script: list[Any] = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    async def chat(self, purpose: str, **kw) -> Any:
        self.calls.append(copy.deepcopy({"purpose": purpose, **kw}))
        if self.script:
            resp = self.script.pop(0)
        else:
            last = next((m for m in reversed(kw.get("messages", [])) if m["role"] == "user"), None)
            text = ""
            if last:
                c = last["content"]
                text = c if isinstance(c, str) else next((b.get("text", "") for b in c if b.get("type") == "text"), "")
            resp = fake_message([fake_text(_("(coach simulado) Recibí: {text}", text=text[:120]))])
        _record(purpose, kw.get("model", "fake"), resp, time.monotonic())
        return resp

    async def parse(self, purpose: str, output_format: type, **kw) -> Any:
        self.calls.append({"purpose": purpose, **kw})
        if self.script:
            return self.script.pop(0)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=None, usage=None)


class _Block(SimpleNamespace):
    def model_dump(self, **_kw) -> dict:
        return {k: v for k, v in vars(self).items()}


def fake_text(text: str) -> _Block:
    return _Block(type="text", text=text)


def fake_tool_use(id_: str, name: str, input_: dict) -> _Block:
    return _Block(type="tool_use", id=id_, name=name, input=input_)


def fake_message(content: list, stop_reason: str | None = None) -> SimpleNamespace:
    sr = stop_reason or ("tool_use" if any(b.type == "tool_use" for b in content) else "end_turn")
    usage = SimpleNamespace(input_tokens=0, output_tokens=0, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    return SimpleNamespace(content=content, stop_reason=sr, usage=usage, stop_details=None)


_llm: Any = None


def get_llm() -> Any:
    global _llm
    if _llm is None:
        _llm = FakeLLM() if get_settings().llm_mode == "fake" else LiveLLM()
    return _llm


def set_llm(llm: Any) -> None:
    global _llm
    _llm = llm
