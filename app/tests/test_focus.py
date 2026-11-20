"""Workout titles say what was trained: a title naming an untrained group is rewritten from the sets."""

from __future__ import annotations

from datetime import date

from factories import make_exercise

from fitos.i18n import _, set_lang
from fitos.training import service as tr
from fitos.training.focus import honest_title, title_groups
from fitos.wa.parser import SetEntry

DAY = date(2026, 11, 20)


def _session(db, items, done):
    for ex_id, muscle in {**{i[0]: i[1] for i in items}}.items():
        make_exercise(db, ex_id, muscle=muscle, tag="machine")
    plan = tr.put_custom_plan(db, DAY, "Pierna + abdomen", [{"exercise": e, "sets": 3} for e, _m in items])
    ws = tr.start_session(db, plan, source="web")
    for it in plan.items:
        if it.exercise_id in done:
            tr.log_sets(db, ws, it, [SetEntry(10, 50)] * done[it.exercise_id])
    return plan, ws


def test_title_groups_reads_spanish_and_english():
    assert title_groups("Pierna + abdomen (suave)") == {"legs", "abs"}
    assert title_groups("Espalda alta + cadera + core") == {"back", "legs", "abs"}
    assert title_groups("Chest + shoulders + biceps") == {"chest", "shoulders", "biceps"}


def test_untrained_abs_rewrites_the_title(db):
    plan, ws = _session(db, [("press", "quads"), ("curl", "biceps"), ("crunch", "abs")],
                        {"press": 3, "curl": 3})
    tr.finish_session(db, ws)
    assert plan.focus_label == "Pierna + Bíceps"


def test_title_stays_when_everything_named_was_trained(db):
    plan, ws = _session(db, [("press", "quads"), ("crunch", "abs"), ("curl", "biceps")],
                        {"press": 3, "crunch": 3, "curl": 3})
    tr.finish_session(db, ws)
    assert plan.focus_label == "Pierna + abdomen"  # extra biceps doesn't make it false


def test_one_set_isnt_a_trained_group_and_composed_titles_translate(db):
    plan, ws = _session(db, [("press", "quads"), ("crunch", "abs")], {"press": 3, "crunch": 1})
    assert honest_title(plan.focus_label, ws) == "Pierna"
    set_lang("en")
    assert _("Pierna + Bíceps + Cardio") == "Legs + Biceps + Cardio"


def test_titles_without_known_groups_stay(db):
    plan, ws = _session(db, [("press", "quads"), ("curl", "biceps")], {"press": 3})
    assert honest_title("Torso A", ws) is None and honest_title("Día libre", ws) is None
