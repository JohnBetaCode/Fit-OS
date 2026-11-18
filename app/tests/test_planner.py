from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta

import pytest
from factories import CASA_TAGS, log_session, make_catalog, make_equipment_profiles, make_profile
from sqlalchemy import select

from fitos.core.models import Exercise, PlannedSession, Program
from fitos.training.planner import (
    NoProgramError,
    build_session,
    create_program_from_template,
    history_for,
    load_templates,
)
from fitos.training.substitution import eligible

MON = date(2026, 11, 16)
ISOLATION_PATTERNS = {"lateral_raise", "chest_fly", "elbow_flexion", "elbow_extension", "knee_extension",
                      "knee_flexion", "calf_raise", "rear_delt", "core_flexion", "core_stability"}
TEMPLATES = ["full_body_3", "upper_lower_4", "ppl_upper_lower_5", "ppl_6"]


@pytest.fixture
def setup(db):
    cat = make_catalog(db)
    casa, gym = make_equipment_profiles(db)
    prof = make_profile(db, schedule={"mon": {"equipment_profile_id": casa.id, "time": "06:30"}})
    return cat, casa, gym, prof


# ── Plantillas ───────────────────────────────────────────────────────────────


def test_templates_shape_and_volume():
    data = load_templates()
    assert set(data["templates"]) == set(TEMPLATES) | {"combo_3"}  # combo_3 is opt-in, tested apart
    assert data["default_for_days"] == {2: "full_body_3", 3: "full_body_3", 4: "upper_lower_4",
                                        5: "ppl_upper_lower_5", 6: "ppl_6"}
    for key in TEMPLATES:
        tpl = data["templates"][key]
        weekly: dict[str, int] = defaultdict(int)
        freq: Counter[str] = Counter()
        for day in tpl["days"]:
            assert day["name"] and day["focus"]
            for s in day["slots"]:
                assert {"slot", "pattern", "muscle", "sets", "rep_min", "rep_max", "target_rir"} <= set(s)
                assert s["target_rir"] == 2
                iso = s["pattern"] in ISOLATION_PATTERNS
                assert (s["rep_min"], s["rep_max"]) == ((10, 15) if iso else (6, 10)), (key, s)
                weekly[s["muscle"]] += s["sets"]
            for m in {s["muscle"] for s in day["slots"]}:
                freq[m] += 1
        back = weekly["lats"] + weekly["upper_back"]
        for m, n in [("chest", weekly["chest"]), ("back", back), ("quads", weekly["quads"]),
                     ("hamstrings", weekly["hamstrings"]), ("delts", weekly["delts"])]:
            assert 8 <= n <= 20, (key, m, n)
        for m in ["chest", "quads", "hamstrings", "delts", "calves", "abs"]:
            assert freq[m] >= 2, (key, m)
        assert freq["lats"] + freq["upper_back"] >= 2


def test_create_program_archives_previous(db):
    p1 = create_program_from_template(db, "upper_lower_4", 4)
    assert p1.status == "active" and [d.name for d in p1.days] == ["Torso A", "Pierna A", "Torso B", "Pierna B"]
    assert all("preferred_exercise_ids" in s for s in p1.days[0].slots)
    p2 = create_program_from_template(db, None, 6)
    assert p2.template_key == "ppl_6" and p1.status == "archived"
    assert db.scalars(select(Program).where(Program.status == "active")).all() == [p2]
    assert create_program_from_template(db, None, 7).template_key == "ppl_6"


def test_no_program_raises(db, setup):
    with pytest.raises(NoProgramError):
        build_session(db, MON)


# ── Plan del día ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", TEMPLATES)
def test_casa_never_proposes_outside_its_tags(db, setup, key):
    _, casa, _, _ = setup
    prog = create_program_from_template(db, key, 4)
    for i in range(len(prog.days)):
        prog.rotation_idx = i
        plan = build_session(db, MON + timedelta(days=7 * i), casa.id)
        assert plan.items, (key, i)
        for it in plan.items:
            ex = db.get(Exercise, it.exercise_id)
            assert eligible(ex, set(CASA_TAGS)), (key, ex.id)
            assert ex.equipment_tag in CASA_TAGS and set(ex.requires) <= set(CASA_TAGS)
        assert len({it.exercise_id for it in plan.items}) == len(plan.items)  # sin repetir en la sesión


def test_plan_uses_schedule_rotation_and_persists(db, setup):
    _, casa, gym, _ = setup
    prog = create_program_from_template(db, "upper_lower_4", 4)
    plan = build_session(db, MON)  # lunes → Casa por el horario
    assert plan.equipment_profile_id == casa.id and plan.focus_label == "Torso A"
    assert plan.status == "proposed" and plan.source == "planner" and plan.program_day_id == prog.days[0].id
    first = plan.items[0]
    assert first.exercise_id == "db_bench"  # básico elegible en casa
    assert (first.sets, first.rep_min, first.rep_max, first.target_rir) == (4, 6, 10, 2)
    assert first.target_weight_kg is None and "elige un peso" in first.target_note
    assert first.slot["pattern"] == "push_horizontal"
    assert prog.rotation_idx == 0
    tue = build_session(db, MON + timedelta(days=1))  # sin horario → perfil por defecto (Gym)
    assert tue.equipment_profile_id == gym.id
    assert tue.items[0].exercise_id == "bb_bench"


def test_existing_proposed_is_returned_or_replaced(db, setup):
    _, casa, gym, _ = setup
    create_program_from_template(db, "full_body_3", 3)
    a = build_session(db, MON)
    assert build_session(db, MON).id == a.id
    b = build_session(db, MON, gym.id, replace=True)
    assert b.id != a.id and a.status == "replaced" and b.equipment_profile_id == gym.id
    assert db.scalars(select(PlannedSession).where(PlannedSession.status == "proposed")).all() == [b]


def test_all_sets_at_rep_max_next_target_goes_up_one_step(db, setup):
    _, casa, _, _ = setup
    create_program_from_template(db, "upper_lower_4", 4)
    log_session(db, MON - timedelta(days=7), "db_bench", [(10, 20, 2)] * 4, equipment_profile_id=casa.id, warmups=1)
    it = build_session(db, MON).items[0]
    assert it.exercise_id == "db_bench" and it.target_weight_kg == 22
    assert "Subimos" in it.target_note


def test_history_preferred_and_recent_demoted(db, setup):
    _, _, gym, _ = setup
    create_program_from_template(db, "upper_lower_4", 4)
    log_session(db, MON - timedelta(days=10), "bb_bench", [(8, 60, 2)], equipment_profile_id=gym.id)
    log_session(db, MON - timedelta(days=10), "db_incline", [(8, 20, 2)], equipment_profile_id=gym.id)
    assert build_session(db, MON, gym.id).items[0].exercise_id == "bb_bench"
    log_session(db, MON - timedelta(days=1), "bb_bench", [(8, 60, 2)], equipment_profile_id=gym.id)
    log_session(db, MON - timedelta(days=10), "db_bench", [(8, 20, 2)], equipment_profile_id=gym.id)
    it = build_session(db, MON, gym.id, replace=True).items[0]
    assert it.exercise_id == "db_bench"  # bb_bench se hizo ayer


def test_disliked_injured_and_preferred(db, setup):
    _, casa, _, prof = setup
    prog = create_program_from_template(db, "upper_lower_4", 4)
    prof.disliked_exercise_ids = ["db_bench"]
    assert build_session(db, MON, casa.id).items[0].exercise_id == "db_incline"
    prof.injuries = [{"area": "hombro", "active": True}]
    assert build_session(db, MON, casa.id, replace=True).items[0].exercise_id == "pushup"
    slots = [dict(s) for s in prog.days[0].slots]
    slots[0]["preferred_exercise_ids"] = ["db_fly"]
    prog.days[0].slots = slots
    prof.injuries, prof.disliked_exercise_ids = [], []
    assert build_session(db, MON, casa.id, replace=True).items[0].exercise_id == "db_fly"


def test_load_ceiling_prefers_harder_variant(db, setup):
    _, casa, _, _ = setup
    prog = create_program_from_template(db, "upper_lower_4", 4)
    prog.rotation_idx = 3  # Pierna B: lunge/quads es el segundo slot
    log_session(db, MON - timedelta(days=7), "db_lunge", [(15, 24, 2)] * 3, equipment_profile_id=casa.id)
    items = build_session(db, MON, casa.id).items
    lunge = next(it for it in items if it.slot["slot"] == "lunge")
    assert lunge.exercise_id == "db_split_squat" and "techo de carga" in lunge.target_note


def test_history_for_groups_and_prefers_profile(db, setup):
    _, casa, gym, _ = setup
    log_session(db, MON - timedelta(days=9), "db_bench", [(10, 20, 2), (9, 20, 2)], equipment_profile_id=casa.id)
    log_session(db, MON - timedelta(days=2), "db_bench", [(8, 30, 2)], equipment_profile_id=gym.id, warmups=2)
    allh = history_for(db, "db_bench")
    assert [h.date for h in allh] == [MON - timedelta(days=2), MON - timedelta(days=9)]
    assert [s.reps for s in allh[0].sets] == [8]  # sin calentamiento
    casa_h = history_for(db, "db_bench", casa.id)
    assert len(casa_h) == 1 and [s.reps for s in casa_h[0].sets] == [10, 9]
    assert len(history_for(db, "db_bench", 999)) == 2  # perfil sin registros → todos
    assert history_for(db, "db_bench", limit=1)[0].equipment_profile_id == gym.id


def test_combo_template_is_a_body_part_split_with_cardio_first():
    """Opt-in split (not a default): each muscle once per rotation, cardio as the first slot, 20–22 sets a day."""
    tpl = load_templates()["templates"]["combo_3"]
    assert len(tpl["days"]) == 3 and "combo_3" not in load_templates()["default_for_days"].values()
    for day in tpl["days"]:
        first, rest = day["slots"][0], day["slots"][1:]
        assert first["pattern"] == "cardio" and first["duration_min"] == [15, 20]
        assert 20 <= sum(s["sets"] for s in rest) <= 22 and len(rest) == 7
