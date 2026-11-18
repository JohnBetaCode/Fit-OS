"""Welcome flow: a new person is sent there from Today, each step saves, and the end leaves a profile, a
schedule and a program; people who already trained, and admins viewing someone, are never redirected."""

from __future__ import annotations

from fastapi.testclient import TestClient

from fitos.body import service as body
from fitos.core.db import session_scope
from fitos.main import app
from fitos.training import service as tr
from fitos.web.routes_welcome import recommended_template


def _client() -> TestClient:
    return TestClient(app, follow_redirects=False)


def test_new_person_goes_through_the_welcome_flow():
    with _client() as c:
        r = c.get("/")
        assert r.status_code == 303 and r.headers["location"] == "/bienvenida"
        page = c.get("/bienvenida")
        assert page.status_code == 200 and "Paso 1 de 4" in page.text
        assert c.post("/bienvenida/1", data={"display_name": "Ana", "sex": "female", "birth_date": "1995-03-14",
                                             "height_cm": "165", "weight_kg": "61,5"}).headers["location"] == "/bienvenida?paso=2"
        c.post("/bienvenida/2", data={"primary_goal": "strength", "experience": "beginner", "inj_knee": "1",
                                      "injury_notes": "menisco"})
        r = c.post("/bienvenida/3", data={})  # no days: stays, with an error
        assert r.headers["location"] == "/bienvenida?paso=3&error=dias"
        c.post("/bienvenida/3", data={"day_mon": "1", "day_wed": "1", "day_fri": "1", "time": "18:00",
                                      "session_minutes": "45"})
        step4 = c.get("/bienvenida?paso=4")
        assert "recomendado" in step4.text
        assert c.post("/bienvenida/4", data={}).headers["location"] == "/bienvenida?paso=5"
        assert "Listo" in c.get("/bienvenida?paso=5").text
        assert c.get("/").status_code == 200  # onboarded: Today shows
    with session_scope() as db:
        p = body.get_profile(db)
        assert (p.display_name, p.sex, p.height_cm, p.primary_goal, p.experience) == ("Ana", "female", 165, "strength", "beginner")
        assert p.injuries[0]["area"] == "knee" and p.onboarded
        assert sorted(p.schedule) == ["fri", "mon", "wed"] and p.days_per_week == 3 and p.session_minutes == 45
        assert {v["time"] for v in p.schedule.values()} == {"18:00"}
        assert body.latest_metric(db).value == 61.5
        prog = tr.active_program(db)
        assert prog.template_key == recommended_template(_tpl(), 3)


def _tpl():
    from fitos.training.planner import load_templates

    return load_templates()


def test_choosing_another_program_and_skipping():
    with _client() as c:
        c.post("/bienvenida/3", data={"day_mon": "1", "day_thu": "1"})
        c.post("/bienvenida/4", data={"template_key": "upper_lower_4"})
    with session_scope() as db:
        assert tr.active_program(db).template_key == "upper_lower_4"
        body.get_profile(db).onboarded = False
    with _client() as c:
        assert c.post("/bienvenida/saltar").headers["location"] == "/"
        assert c.get("/").status_code == 200


def test_recommended_template_clamps_days():
    tpl = {"default_for_days": {2: "a", 3: "b", 6: "c"}, "templates": {}}
    assert recommended_template(tpl, 1) == "a" and recommended_template(tpl, 3) == "b"
    assert recommended_template(tpl, 9) == "c"


def test_footer_shows_version(monkeypatch):
    from fitos.core.settings import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "git_sha", "dc4fe4b0123456789")
    monkeypatch.setattr(s, "git_branch", "main")
    with session_scope() as db:
        body.ensure_defaults(db).onboarded = True
    with _client() as c:
        assert "main · dc4fe4b" in c.get("/").text  # the test account is an admin
