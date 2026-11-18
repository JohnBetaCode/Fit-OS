"""Body map: recovery per muscle (fatigue from recent hard sets that fades with time) and this week's coverage."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from factories import log_session, make_exercise
from fastapi.testclient import TestClient

from fitos.main import app
from fitos.training import assist, recovery

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def _by_key(rows):
    return {r.key: r for r in rows}


def _squats(db, sets: int, hours_ago: float, rir: float = 2):
    make_exercise(db, "squat", muscle="quads", pattern="squat", tag="barbell", secondary=["glutes"])
    start = NOW - timedelta(hours=hours_ago)
    log_session(db, start.date(), "squat", [(8, 100, rir)] * sets, started_at=start)


def test_fatigue_fades_from_red_to_orange_to_green(db):
    _squats(db, 10, hours_ago=1)
    quads = _by_key(recovery.recovery(db, now=NOW))["quads"]
    assert quads.status == "fatigued" and 60 < quads.ready_in_h < 80  # red
    assert _by_key(recovery.recovery(db, now=NOW + timedelta(hours=30)))["quads"].status == "recovering"  # orange
    assert _by_key(recovery.recovery(db, now=NOW + timedelta(hours=80)))["quads"].status == "ready"


def test_secondary_muscles_get_half_and_easy_sets_dont_count(db):
    _squats(db, 10, hours_ago=1)
    rows = _by_key(recovery.recovery(db, now=NOW))
    assert abs(rows["glutes"].fatigue - rows["quads"].fatigue / 2) < 0.01
    assert rows["chest"].status == "ready" and rows["chest"].last_trained is None


def test_sets_far_from_failure_are_not_hard(db):
    _squats(db, 10, hours_ago=1, rir=6)
    assert _by_key(recovery.recovery(db, now=NOW))["quads"].fatigue == 0


def test_week_levels_and_ready_groups(db):
    levels = recovery.week_levels({"chest": 12, "lats": 3, "quads": 7, "delts": 25}, 10, 20)
    assert (levels["chest"], levels["lats"], levels["quads"], levels["delts"], levels["calves"]) == (
        "target", "low", "mid", "over", "none")
    _squats(db, 12, hours_ago=2)
    ready = recovery.ready_groups(recovery.recovery(db, now=NOW), assist.GROUPS)
    assert "legs" not in ready and "glutes" not in ready and "chest" in ready and "full" not in ready


def test_body_page_both_views_and_today_card(db):
    _squats(db, 10, hours_ago=3)
    from fitos.body.service import ensure_defaults

    ensure_defaults(db).onboarded = True
    db.commit()
    with TestClient(app) as c:
        rec = c.get("/progreso/cuerpo")
        assert rec.status_code == 200 and "<svg" in rec.text and 'data-m="quads"' in rec.text
        assert "En recuperación" in rec.text and "Listos para entrenar" in rec.text
        week = c.get("/progreso/cuerpo?vista=semana")
        assert week.status_code == 200 and "Lo que falta esta semana" in week.text and "Sin entrenar" in week.text
        assert "Mapa de recuperación por músculo" in c.get("/").text
        setup = c.get("/entreno?nuevo=1&grupos=chest,back")
        assert 'name="g_chest" value="1" class="sr-only" checked' in setup.text


def test_today_without_history_has_no_map(db):
    from fitos.body.service import ensure_defaults

    ensure_defaults(db).onboarded = True
    db.commit()
    with TestClient(app) as c:
        assert "Mapa de recuperación por músculo" not in c.get("/").text


_ = date


def test_figure_follows_the_profile_sex(db):
    from fitos.body.service import ensure_defaults

    prof = ensure_defaults(db)
    prof.onboarded = True
    prof.sex = "male"
    db.commit()
    with TestClient(app) as c:
        assert 'viewBox="0 0 724 1448"' in c.get("/progreso/cuerpo").text
        prof.sex = "female"
        db.commit()
        page = c.get("/progreso/cuerpo").text
        assert 'viewBox="-50 -40 734 1538"' in page and 'data-m="lats upper_back"' in page


def test_shared_region_shows_the_worst_muscle():
    from fitos.web.bodymap import figures

    fills = {"lats": "red", "upper_back": "green"}
    back = figures("male", fills, {}, {"lats": 9.0, "upper_back": 1.0})[1]
    region = next(p for p in back["parts"] if p["muscles"] == ["lats", "upper_back"])
    assert region["fill"] == "red"
