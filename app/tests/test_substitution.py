from __future__ import annotations

import pytest
from factories import CASA_TAGS, make_catalog, make_exercise

from fitos.training.planner import ALL_TAGS
from fitos.training.substitution import (
    active_injured_joints,
    eligible,
    rank_alternatives,
    score,
    word_to_tag,
)


@pytest.fixture
def cat(db):
    return make_catalog(db)


def ids(ranked):
    return [ex.id for ex, _ in ranked]


def test_eligible(cat):
    casa = set(CASA_TAGS)
    assert eligible(cat["db_bench"], casa)
    assert eligible(cat["db_incline"], casa)
    assert not eligible(cat["bb_bench"], casa)
    assert not eligible(cat["pullup"], casa)  # bodyweight pero pide barra de dominadas
    assert eligible(cat["pullup"], ALL_TAGS)
    assert not eligible(cat["disabled_press"], ALL_TAGS)


def test_ranking_order_gym(db, cat):
    ranked = rank_alternatives(db, cat["bb_bench"], ALL_TAGS, limit=10)
    # Mismo patrón + músculo + secundarios + mecánica + lateralidad + básico gana.
    assert ids(ranked)[0] == "db_bench"
    s = dict((ex.id, sc) for ex, sc in ranked)
    assert s["db_bench"] == 50 + 30 + 10 + 10 + 5 + 8 + 8  # +8 carga externa → carga externa
    assert s["db_incline"] == 50 + 30 + 10 + 10 + 5 + 8  # mismos secundarios (Jaccard 1)
    assert s["db_bench"] > s["db_incline"] > s["pushup"] > s["cable_fly"]
    assert "disabled_press" not in s and "bb_bench" not in s
    # dips: mismo patrón, otro músculo → entra; db_fly: mismo músculo, otro patrón → entra.
    assert {"dips", "db_fly"} <= set(s)
    assert [sc for _, sc in ranked] == sorted((sc for _, sc in ranked), reverse=True)


def test_casa_filters_equipment(db, cat):
    ranked = rank_alternatives(db, cat["bb_bench"], set(CASA_TAGS), limit=20)
    assert ids(ranked)[:3] == ["db_bench", "db_incline", "pushup"]
    assert all(eligible(ex, set(CASA_TAGS)) for ex, _ in ranked)
    assert "cable_fly" not in ids(ranked) and "dips" not in ids(ranked)


def test_excluded_tag_busy_station_and_exclude_ids(db, cat):
    ranked = rank_alternatives(db, cat["bb_bench"], ALL_TAGS, excluded_tags={"dumbbell"}, limit=20)
    assert not any(ex.equipment_tag == "dumbbell" for ex, _ in ranked)
    ranked = rank_alternatives(db, cat["lat_pulldown"], ALL_TAGS, busy_stations={"pullup_bar"}, limit=20)
    assert "pullup" not in ids(ranked) and "db_pullover" in ids(ranked)
    ranked = rank_alternatives(db, cat["bb_bench"], ALL_TAGS, exclude_ids={"db_bench"})
    assert ids(ranked)[0] == "db_incline"


def test_injured_joints_filter(db, cat):
    ranked = rank_alternatives(db, cat["bb_bench"], ALL_TAGS, injured_joints={"shoulder"}, limit=20)
    assert not any("shoulder" in ex.stress_joints for ex, _ in ranked)
    assert ids(ranked)[0] == "pushup"


def test_history_and_recent_adjust_score(db, cat):
    base = rank_alternatives(db, cat["bb_bench"], ALL_TAGS, limit=20)
    assert ids(base)[:2] == ["db_bench", "db_incline"]
    ranked = rank_alternatives(db, cat["bb_bench"], ALL_TAGS, recent_ids={"db_bench"}, history_ids={"db_incline"})
    # db_incline 105 + 6; pushup 100; db_bench 113 − 15.
    assert ids(ranked)[:2] == ["db_incline", "db_bench"]  # el bono de carga mantiene mancuernas sobre flexiones
    s = dict((ex.id, sc) for ex, sc in ranked)
    assert s["db_bench"] == 121 - 15 and s["db_incline"] == 113 + 6


def test_limit_and_no_candidates(db, cat):
    assert len(rank_alternatives(db, cat["bb_bench"], ALL_TAGS, limit=2)) == 2
    lonely = make_exercise(db, "lonely", muscle="forearms", pattern="carry", tag="other")
    assert rank_alternatives(db, lonely, ALL_TAGS) == []


def test_jaccard_partial():
    a = make_exercise(id="a", secondary=["triceps", "delts"])
    b = make_exercise(id="b", secondary=["triceps"], mechanic="isolation", laterality="unilateral")
    assert score(a, b) == 50 + 30 + 5 + 8  # Jaccard 1/2 → +5; ambos con mancuerna → +8


@pytest.mark.parametrize(
    ("text", "tag"),
    [
        ("mancuernas", "dumbbell"),
        ("No tengo MANCUERNAS", "dumbbell"),
        ("barra", "barbell"),
        ("no hay barra de dominadas", "pullup_bar"),
        ("sin barra fija", "pullup_bar"),
        ("polea", "cable"),
        ("cable", "cable"),
        ("máquina", "machine"),
        ("maquina smith", "smith"),
        ("banco", "bench_flat"),
        ("banco inclinado", "bench_incline"),
        ("paralelas", "dip_bars"),
        ("smith", "smith"),
        ("rack", "rack"),
        ("kettlebell", "kettlebell"),
        ("pesa rusa", "kettlebell"),
        ("banda", "band"),
        ("ligas", "band"),
        ("barra Z", "ez_bar"),
        ("cajón", "box"),
        ("balón medicinal", "medicine_ball"),
        ("nada que ver", None),
        ("", None),
    ],
)
def test_word_to_tag(text, tag):
    assert word_to_tag(text) == tag


def test_active_injured_joints():
    injuries = [
        {"area": "Hombro derecho", "severity": "leve"},
        {"area": "rodilla", "active": False},
        {"area": "lower_back", "active": True},
        {"area": "Muñeca"},
        {"area": "oreja"},
    ]
    assert active_injured_joints(injuries) == {"shoulder", "lower_back", "wrist"}
    assert active_injured_joints(None) == set()
