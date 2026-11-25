"""Progress → Recomposition: fat and lean mass in kg, the 14-day trend, an honest verdict, and the morning
weigh-in nudge."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi.testclient import TestClient

from fitos.body import composition as comp
from fitos.body import service as body
from fitos.core.timeutil import local_today
from fitos.main import app

D0 = date(2026, 9, 1)


def _weigh(db, day: date, w: float, bf: float | None, source: str = "renpho"):
    body.log_metric(db, "weight_kg", w, on=day, source=source)
    if bf is not None:
        body.log_metric(db, "body_fat_pct", bf, on=day, source=source)


def test_points_pair_weight_and_fat_and_prefer_the_scale(db):
    _weigh(db, D0, 80.0, 15.0)
    _weigh(db, D0, 81.0, None, source="manual")  # same day by hand: the scale's reading wins
    _weigh(db, D0 + timedelta(days=1), 80.5, None)  # no body fat: not a point
    [p] = comp.points(db)
    assert (p.weight, p.fat_kg, p.lean_kg) == (80.0, 12.0, 68.0)


def test_verdicts(db):
    for i in range(30):  # fat 12 → ~10.6 kg, lean steady ~68: recomposition
        _weigh(db, D0 + timedelta(days=i), 80.0 - i * 0.05, 15.0 - i * 0.05)
    s = comp.summary(comp.points(db))
    assert s["verdict"] == "recomp" and s["d_fat"] < 0 and abs(s["d_lean"]) < 0.3 and s["n"] == 30
    avg = comp.smoothed(comp.points(db), "fat_kg")
    assert len(avg) == 30 and avg[0][1] == 12.0
    assert comp.summary([])["verdict"] == "none"
    few = comp.points(db)[:3]
    assert comp.summary(few)["verdict"] == "few"  # start and end overlap: nothing to compare


def test_sparse_weigh_ins_use_the_three_nearest_each_end(db):
    for i in range(0, 60, 10):  # one weigh-in every 10 days: never 2 in a 14-day window
        _weigh(db, D0 + timedelta(days=i), 80.0, 15.0 - i * 0.03)
    s = comp.summary(comp.points(db))
    assert s["verdict"] != "few" and s["d_fat"] < 0
    far = [comp.Point(D0, 80, 12, 68), comp.Point(D0 + timedelta(days=100), 80, 11, 69),
           comp.Point(D0 + timedelta(days=200), 80, 10, 70)]
    assert comp.summary(far)["verdict"] == "few"  # 100 days apart: too far to average


def test_fat_gain_is_called_out(db):
    for i in range(30):
        _weigh(db, D0 + timedelta(days=i), 80.0 + i * 0.05, 14.0 + i * 0.06)
    s = comp.summary(comp.points(db))
    assert s["verdict"] in ("fat_gain", "bulk") and s["d_fat"] > 0


def test_weigh_in_nudge(db):
    today = date(2026, 11, 25)
    assert comp.weigh_in_nudge(db, today) is None  # never weighed in: no nagging
    _weigh(db, today - timedelta(days=2), 80.0, 15.0)
    assert comp.weigh_in_nudge(db, today) is None
    assert "hace 5 días" in comp.weigh_in_nudge(db, today + timedelta(days=3))


def test_progress_shows_recomposition_with_ranges(db):
    today = local_today()
    for i in range(20):
        _weigh(db, today - timedelta(days=40 - i * 2), 80.0, 15.0 - i * 0.1)
    db.commit()
    with TestClient(app) as c:
        page = c.get("/progreso").text
        assert "Recomposición" in page and 'id="fat-kg"' in page and "Masa magra" in page
        assert "20 pesajes con % de grasa" in page
        assert c.get("/progreso", params={"rango": "1a"}).status_code == 200
        assert c.get("/progreso", params={"rango": "raro"}).status_code == 200  # unknown range → 90 days
