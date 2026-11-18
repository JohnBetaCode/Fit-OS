"""The dashboard in English: every tab renders, without leftover Spanish copy, and Settings switches language."""

from __future__ import annotations

from datetime import date, timedelta
from html import unescape

import pytest
from factories import log_session, make_catalog, make_equipment_profiles, make_profile
from fastapi.testclient import TestClient

from fitos.core import prefs
from fitos.core.db import session_scope
from fitos.core.timeutil import day_letters, fmt_date, local_today
from fitos.i18n import lang, set_lang
from fitos.main import app
from fitos.training.planner import create_program_from_template

DAY = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

# path → (Spanish that must be gone, English that must be there)
PAGES = {
    "/": (["Sesión de hoy", "Semana", "Avisos", "Últimos récords", "Entrenos"],
          ["Today's workout", "Week", "Alerts", "Latest PRs", "Workouts"]),
    "/entrenos": (["Tonelaje", "en curso", "Entrenos"], ["Workouts", "sets"]),
    "/progreso": (["Sets duros por músculo", "Constancia", "Peso corporal", "Anotar", "Récords"],
                  ["Hard sets per muscle", "Consistency", "Body weight", "Log", "PRs"]),
    "/ejercicios": (["Buscar ejercicio", "Todos los músculos", "Solo básicos", "Animaciones"],
                    ["Search exercises", "All muscles", "Staples only", "Animations"]),
    "/examenes": (["Fuera de meta", "Último examen", "Resumen", "Nutrición"],
                  ["Off target", "Last test", "Overview", "Nutrition"]),
    "/examenes/nutricion": (["Pautas de alimentos", "Resumen"], ["Food guidance", "Overview"]),
    "/coach": (["Conversaciones", "Enviar", "Escribe al coach"], ["Conversations", "Send", "Message the coach"]),
    "/perfil": (["Guardar perfil", "Días de entreno", "Lesiones y molestias", "Hombre", "Perfiles de equipo"],
                ["Save profile", "Training days", "Injuries and niggles", "Male", "Equipment profiles"]),
    "/integraciones": (["Próximamente", "Gasto de hoy", "Integraciones"], ["Coming soon", "Spent today", "Integrations"]),
    "/ajustes": (["Guardar", "Salud del sistema", "Silencio desde", "Programa"],
                 ["Save", "System health", "Quiet from", "Program", "Language"]),
    "/mas": (["Ejercicios", "Ajustes", "Integraciones"], ["Exercises", "Settings", "Integrations"]),
}
NAV_ES = ["Hoy", "Entrenos", "Progreso", "Exámenes", "Más"]


@pytest.fixture
def world():
    with session_scope() as db:
        make_catalog(db)
        _casa, gym = make_equipment_profiles(db)
        make_profile(db, sex="male", days_per_week=4, onboarded=True, injuries=[], disliked_exercise_ids=[],
                     schedule={d: {"equipment_profile_id": gym.id, "time": "06:30"} for d in DAY})
        create_program_from_template(db, None, 4)
        today = local_today()
        log_session(db, today - timedelta(days=7), "db_curl", [(10, 12.0, 2), (9, 12.0, 1)])
        log_session(db, today - timedelta(days=2), "db_curl", [(10, 14.0, 2), (10, 14.0, 1)],
                    pr_flags={0: ["weight", "e1rm"]})
        prefs.set_(db, "language", "en")


@pytest.mark.parametrize("path", list(PAGES))
def test_tabs_in_english(world, path):
    spanish, english = PAGES[path]
    with TestClient(app) as c:
        r = c.get(path)
    assert r.status_code == 200, r.text[:500]
    html = unescape(r.text)
    assert '<html lang="en"' in html
    for word in spanish:
        assert f">{word}<" not in html and f">{word} " not in html and f'"{word}' not in html, (path, word)
    for word in english:
        assert word in html, (path, word)
    for word in NAV_ES:  # bottom and top navigation
        assert f">{word}\n" not in html and f">{word}</a>" not in html, (path, word)


def test_session_detail_and_dates_in_english(world):
    with session_scope() as db:
        from sqlalchemy import select

        from fitos.core.models import WorkoutSession

        ws = db.scalars(select(WorkoutSession)).first()
        sid, day = ws.id, ws.date
    with TestClient(app) as c:
        html = c.get(f"/entrenos/{sid}").text
    assert "Volume" in html and "Delete this set?" in html and "Tonelaje" not in html
    assert fmt_date(day, "en") in html


def test_date_helpers():
    sun = date(2026, 11, 15)
    assert fmt_date(sun, "en") == "Sun 15 Nov"
    assert fmt_date(sun, "es") == "dom 15 nov"
    assert day_letters("en") == "MTWTFSS" and day_letters("es") == "LMXJVSD"
    set_lang("en")
    assert fmt_date(sun) == "Sun 15 Nov"
    set_lang("es")
    assert fmt_date(sun) == "dom 15 nov"


def test_settings_switches_language(world):
    with session_scope() as db:
        prefs.set_(db, "language", "es")
    with TestClient(app) as c:
        page = c.get("/ajustes").text
        assert '<html lang="es"' in page and 'name="language"' in page and "Guardar" in page
        r = c.post("/ajustes", data={"language": "en", "morning_hour": "07:15"}, follow_redirects=False)
        assert r.status_code == 303
        assert lang() == "en"
        page = c.get("/ajustes?saved=1").text
        assert '<html lang="en"' in page and "Saved." in page and ">Settings</a>" in page
        assert '<option value="en" lang="en" selected>English</option>' in page
        assert "Today's workout" in unescape(c.get("/").text)
        c.post("/ajustes", data={"language": "es"})
        assert lang() == "es"
        assert "Sesión de hoy" in c.get("/").text
        c.post("/ajustes", data={"language": "xx"})  # unknown codes are ignored
        assert lang() == "es"


def test_labs_pages_in_english(db):
    from test_labs import BIRTH, FIXTURE  # the synthetic lab seed

    from fitos.core.models import Profile
    from fitos.labs import service
    from fitos.labs.seed_import import import_seed_csv

    service.ensure_biomarkers(db)
    db.add(Profile(id=1, sex="male", birth_date=BIRTH))
    import_seed_csv(db, FIXTURE)
    db.commit()
    prefs.set_(db, "language", "en")
    db.commit()
    with TestClient(app) as c:
        overview = unescape(c.get("/examenes").text)
        detail = unescape(c.get("/examenes/ldl_c").text)
        nutrition = c.get("/examenes/nutricion")
    assert "to confirm" in overview and "por confirmar" not in overview and "Off target" in overview
    assert "labs-chart-i18n" in detail and '"months": ["Jan"' in detail and "Ranges" in detail
    assert ">Valores<" not in detail and ">Rangos<" not in detail
    assert nutrition.status_code == 200
