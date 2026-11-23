"""CLI for planning by hand or with the plan-day skill: `fitos context`, `fitos catalog search`,
`fitos plan put --dry-run` and `program_day`."""

from __future__ import annotations

import json
from datetime import date

from factories import log_session, make_catalog, make_equipment_profiles, make_profile

from fitos.cli import main
from fitos.core.models import PlannedSession
from fitos.training import service as tr
from fitos.training.planner import create_program_from_template


def _world(db):
    make_profile(db, onboarded=True)
    make_equipment_profiles(db)
    make_catalog(db)
    create_program_from_template(db, "upper_lower_4", 4)
    log_session(db, date(2026, 11, 16), "bb_squat", [(8, 100, 2), (8, 100, 1)])
    db.commit()


def test_context_is_json_with_everything_a_coach_needs(db, capsys):
    _world(db)
    assert main(["context", "--date", "2026-11-18"]) == 0
    data = json.loads(capsys.readouterr().out)
    for key in ("profile", "equipment", "program", "recovery", "week", "exercises", "sessions", "plans"):
        assert key in data
    assert data["program"]["next_day"]["name"]
    squat = next(e for e in data["exercises"] if e["id"] == "bb_squat")
    assert squat["last_top"]["kg"] == 100 and squat["sessions"] == 1
    assert data["sessions"][0]["exercises"][0]["sets"][0] == {"reps": 8, "kg": 100.0, "rir": 2.0}
    assert {r["muscle"] for r in data["recovery"]} >= {"quads", "chest"}


def test_catalog_search_filters(db, capsys):
    _world(db)
    assert main(["catalog", "search", "--muscle", "chest", "--place", "casa"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines and all("\tchest\t" in ln for ln in lines)
    assert not any("\tbarbell\t" in ln or "\tcable\t" in ln for ln in lines)  # not at home


def test_plan_put_dry_run_and_program_day(db, tmp_path, capsys):
    _world(db)
    spec = tmp_path / "plan.yaml"
    spec.write_text("date: 2026-11-19\nfocus: Pierna\nprogram_day: next\nitems:\n"
                    "  - {exercise: leg_press, sets: 3, reps: [8, 12], weight_kg: 120}\n"
                    "  - {exercise: leg_curl, sets: 3, reps: [10, 12]}\n", encoding="utf-8")
    assert main(["plan", "put", str(spec), "--dry-run"]) == 0
    assert "dry run" in capsys.readouterr().out
    assert db.query(PlannedSession).filter_by(date=date(2026, 11, 19)).count() == 0
    assert main(["plan", "put", str(spec)]) == 0
    plan = tr.plan_for(db, date(2026, 11, 19))
    prog = tr.active_program(db)
    assert plan.program_day_id == prog.days[prog.rotation_idx].id and len(plan.items) == 2


def test_context_live_shows_the_workout_in_progress(db, tmp_path, capsys):
    from fitos.wa.parser import SetEntry

    _world(db)
    spec = tmp_path / "plan.yaml"
    spec.write_text("date: 2026-11-19\nfocus: Pierna\nitems:\n"
                    "  - {exercise: leg_press, sets: 3, reps: [8, 12], weight_kg: 120}\n"
                    "  - {exercise: leg_curl, sets: 3, reps: [10, 12]}\n", encoding="utf-8")
    assert main(["plan", "put", str(spec)]) == 0
    capsys.readouterr()
    assert main(["context", "--date", "2026-11-19"]) == 0
    assert json.loads(capsys.readouterr().out)["live"] is None  # nothing started yet
    plan = tr.plan_for(db, date(2026, 11, 19))
    ws = tr.start_session(db, plan, source="web")
    first = plan.items[0]
    tr.log_sets(db, ws, first, [SetEntry(10, 120.0, 2), SetEntry(9, 120.0, 1)], raw_text="test")
    db.commit()
    assert main(["context", "--date", "2026-11-19"]) == 0
    live = json.loads(capsys.readouterr().out)["live"]
    assert live["state"] == "active" and live["focus"] == "Pierna"
    press = next(i for i in live["items"] if i["id"] == "leg_press")
    assert press["sets"] == [{"reps": 10, "kg": 120.0, "rir": 2.0}, {"reps": 9, "kg": 120.0, "rir": 1.0}]
    assert press["planned"] == {"sets": 3, "reps": [8, 12], "kg": 120.0}
    assert live["totals"]["work_sets"] == 2 and live["totals"]["volume_kg"] == 2280.0
    assert next(i for i in live["items"] if i["id"] == "leg_curl")["sets"] == []
