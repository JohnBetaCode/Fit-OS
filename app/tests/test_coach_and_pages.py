"""Coach (loop de tools con FakeLLM) y humo de todas las pestañas."""

from __future__ import annotations

import pytest
from factories import make_catalog, make_equipment_profiles, make_profile
from fastapi.testclient import TestClient
from sqlalchemy import select

from fitos.coach import agent
from fitos.coach import llm as llm_mod
from fitos.core.db import session_scope
from fitos.core.models import ChatMessage, LlmCall
from fitos.core.settings import get_settings
from fitos.main import app
from fitos.training.planner import create_program_from_template

DAY = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@pytest.fixture
def world():
    with session_scope() as db:
        make_catalog(db)
        _casa, gym = make_equipment_profiles(db)
        make_profile(db, sex="male", days_per_week=4, onboarded=True, injuries=[], disliked_exercise_ids=[],
                     schedule={d: {"equipment_profile_id": gym.id, "time": "06:30"} for d in DAY})
        create_program_from_template(db, None, 4)


@pytest.fixture
def fake_llm(monkeypatch):
    monkeypatch.setattr(get_settings(), "llm_mode", "fake")
    fake = llm_mod.FakeLLM()
    llm_mod.set_llm(fake)
    yield fake
    llm_mod.set_llm(None)


async def test_coach_tool_loop_appends_and_validates(world, fake_llm):
    fake_llm.script = [
        llm_mod.fake_message([llm_mod.fake_tool_use("t1", "get_profile", {}),
                              llm_mod.fake_tool_use("t2", "log_set", {"sets": [{"reps": 500, "weight_kg": 10,
                                                                                "rir": None}]})]),
        llm_mod.fake_message([llm_mod.fake_text("Vas bien: 4 días por semana.")]),
    ]
    reply = await agent.coach_reply("¿cómo voy?", channel="web")
    assert reply == "Vas bien: 4 días por semana."
    second = fake_llm.calls[1]["messages"]
    results = second[-1]["content"]
    assert [r["tool_use_id"] for r in results] == ["t1", "t2"]  # todos los resultados en un solo mensaje
    assert results[1].get("is_error") is True  # log_set fuera de sesión/rango → error, no escritura
    assert any(m["role"] == "system" for m in second)  # ficha como mensaje system tras el del usuario
    with session_scope() as db:
        roles = [m.role for m in db.scalars(select(ChatMessage).order_by(ChatMessage.id))]
        assert roles == ["user", "system", "assistant", "user", "assistant"]
        assert db.scalar(select(LlmCall.id)) is not None


async def test_coach_history_is_append_only(world, fake_llm):
    await agent.coach_reply("hola", channel="wa")
    first = fake_llm.calls[0]["messages"]
    await agent.coach_reply("¿y hoy?", channel="wa")
    second = fake_llm.calls[1]["messages"]
    assert second[: len(first)] == first  # el prefijo no se edita


async def test_coach_off_returns_none(world):
    assert await agent.coach_reply("hola") is None


@pytest.mark.parametrize("path", ["/", "/entrenos", "/progreso", "/ejercicios", "/examenes", "/examenes/nutricion",
                                  "/coach", "/perfil", "/integraciones", "/ajustes", "/mas", "/healthz"])
def test_pages_render(world, path):
    with TestClient(app) as c:
        r = c.get(path)
        assert r.status_code == 200, r.text[:500]


def test_build_plan_from_web_and_profile_save(world):
    with TestClient(app) as c:
        assert c.post("/hoy/plan", data={"action": "build"}, follow_redirects=False).status_code == 303
        page = c.get("/").text
        assert "Sesión de hoy" in page
        r = c.post("/perfil", data={"sex": "male", "birth_date": "1990-01-01", "height_cm": "180",
                                    "experience": "intermediate", "primary_goal": "strength", "session_minutes": "60",
                                    "day_mon": "on", "ep_mon": "2", "time_mon": "07:00", "day_thu": "on",
                                    "ep_thu": "2", "time_thu": "07:00", "inj_shoulder": "on", "sev_shoulder": "leve"},
                   follow_redirects=False)
        assert r.status_code == 303
        assert "Guardado" in c.get("/perfil?saved=1").text
        assert c.post("/ajustes", data={"morning_hour": "07:15", "clip_policy": "always"},
                      follow_redirects=False).status_code == 303
