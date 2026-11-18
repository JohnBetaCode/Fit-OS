"""Export: the ZIP from Settings and the CLI, and the printable report."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import timedelta

import pytest
from factories import log_session, make_catalog, make_equipment_profiles, make_profile
from fastapi.testclient import TestClient

from fitos.body import service as body
from fitos.core import prefs
from fitos.core.db import session_scope
from fitos.core.timeutil import local_today
from fitos.main import app
from fitos.training import service as tr


@pytest.fixture
def world():
    with session_scope() as db:
        make_catalog(db)
        _casa, gym = make_equipment_profiles(db)
        p = make_profile(db, sex="male", days_per_week=4, onboarded=True, injuries=[], disliked_exercise_ids=[])
        p.display_name, p.primary_goal = "Test Lifter", "recomp"
        today = local_today()
        log_session(db, today - timedelta(days=8), "bb_bench", [(8, 70, 2), (8, 70, 2)], equipment_profile_id=gym.id)
        log_session(db, today - timedelta(days=1), "bb_bench", [(8, 75, 2)], equipment_profile_id=gym.id)
        tr.recompute_prs(db, ["bb_bench"])
        body.log_metric(db, "weight_kg", 81.0, today - timedelta(days=8))
        body.log_metric(db, "weight_kg", 80.4, today)


def test_zip_has_everything(world):
    with TestClient(app) as c:
        r = c.get("/ajustes/exportar")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"] and r.headers["cache-control"] == "no-store"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = {n.split("/", 1)[1] for n in z.namelist()}
    for f in ("README.txt", "workouts_sets.csv", "body_metrics.csv", "labs.csv", "fit-os.json", "report.html",
              "tables/workout_sets.csv", "tables/profile.csv"):
        assert f in names, f
    root = z.namelist()[0].split("/")[0]
    log = z.read(f"{root}/workouts_sets.csv").decode()
    assert log.splitlines()[0].startswith("date,workout_id,focus,exercise_id")
    assert len(log.splitlines()) == 4 and "weight" in log  # header + 3 sets; the PR flag is there
    data = json.loads(z.read(f"{root}/fit-os.json"))
    assert len(data["tables"]["workout_sets"]) == 3 and data["tables"]["profile"][0]["primary_goal"] == "recomp"
    assert "Test Lifter" in z.read(f"{root}/report.html").decode()


def test_report_in_both_languages(world):
    with TestClient(app) as c:
        html = c.get("/ajustes/reporte").text
        assert "Reporte de progreso" in html and "Recomposición" in html and "80.4 kg" in html
        with session_scope() as db:
            prefs.set_(db, "language", "en")
        html = c.get("/ajustes/reporte").text
        assert "Progress report" in html and "Recomposition" in html and "Last 8 weeks" in html
        assert "Export everything (ZIP)" in c.get("/ajustes").text


def test_cli_export(world, tmp_path):
    from fitos.cli import main

    out = tmp_path / "x" / "export.zip"
    assert main(["export", str(out)]) == 0
    assert zipfile.is_zipfile(out) and (out.stat().st_mode & 0o777) == 0o600
