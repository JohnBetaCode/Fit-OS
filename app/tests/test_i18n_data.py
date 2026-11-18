"""Data-like copy has English: label dicts, program templates, biomarker texts, lab/demo strings; and the data
accessors (exercise names and steps, biomarker descriptions, lab views) follow the language."""

from __future__ import annotations

import ast
from datetime import date
from pathlib import Path

import pytest
import yaml
from factories import make_catalog
from sqlalchemy import select

from fitos import demo
from fitos.body import service as body
from fitos.catalog import service as catalog
from fitos.catalog.importer import import_catalog
from fitos.catalog.names import title_en
from fitos.core.db import session_scope
from fitos.core.models import Biomarker, Exercise, Profile
from fitos.i18n import en_catalog, set_lang
from fitos.labs import ranges as rg
from fitos.labs import seed_import
from fitos.labs import service as labs

APP = Path(__file__).resolve().parents[1]
REPO_CATALOG = APP.parent / "catalog"
FIX = Path(__file__).parent / "fixtures" / "catalog"


def _missing(values) -> list[str]:
    cat = en_catalog()
    return sorted({v for v in values if v not in cat})


def test_label_dicts_have_english():
    values = [v for table in catalog.LABEL_TABLES.values() for v in table.values()]
    values += [v for table in body.LABEL_TABLES.values() for v in table.values()]
    values += [*rg.STATUS_LABELS.values(), *labs.CATEGORIES, *body.DEFAULT_PROFILE_NAMES]
    assert not _missing(values)


def test_program_templates_have_english():
    data = yaml.safe_load((REPO_CATALOG / "program_templates.yaml").read_text("utf-8"))
    values = []
    for t in data["templates"].values():
        values.append(t["name"])
        values += [d["name"] for d in t["days"]]
    assert values and not _missing(values)


def test_biomarkers_have_english_texts():
    items = yaml.safe_load((REPO_CATALOG / "biomarkers.yaml").read_text("utf-8"))["biomarkers"]
    assert all(b.get("name_en") and b.get("description_en") for b in items if b.get("description_es"))
    sources = [r["source"] for b in items for r in b.get("ranges") or [] if r.get("source")]
    labels = [r["label"] for b in items for r in b.get("ranges") or [] if r.get("label")]
    assert sources and not _missing(sources + labels)


def test_stored_lab_and_demo_copy_has_english():
    values = [*seed_import.REVIEW.values(), *seed_import.TARGET_LABELS.values(), seed_import.LAB_NAME,
              demo.DEMO_LAB_NAME, demo.DEMO_NOTE_RAW, *(lbl for _, _, lbl in demo.DEMO_TARGETS.values())]
    for pos, neg in demo.DEMO_NOTES.values():
        values += pos + neg
    assert not _missing(values)


def _area_calls() -> dict[str, str]:
    """`_("…")` literals in this area, implicit concatenation included (the regex in test_i18n skips those)."""
    files = [p for d in ("labs", "body", "catalog") for p in (APP / "fitos" / d).rglob("*.py")]
    out = {}
    for path in [*files, APP / "fitos" / "demo.py"]:
        for node in ast.walk(ast.parse(path.read_text("utf-8"))):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_"
                    and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                out[node.args[0].value] = str(path)
    return out


def test_area_literals_have_english():
    calls = _area_calls()
    assert calls and not _missing(calls)


# ── Accessors ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("raw", "nice"), [
    ("dumbbell bench press", "Dumbbell Bench Press"),
    ("barbell close-grip bench press", "Barbell Close-Grip Bench Press"),
    ("lever seated hip abduction (on a bench)", "Lever Seated Hip Abduction (On a Bench)"),
    ("ez barbell curl with a band", "EZ Barbell Curl with a Band"),
    ("3/4 sit-up", "3/4 Sit-Up"),
    ("Already Titled", "Already Titled"),
])
def test_title_en(raw, nice):
    assert title_en(raw) == nice


def test_exercise_name_and_steps_follow_language(db):
    import_catalog(db, FIX / "src", FIX / "enrichment.json", REPO_CATALOG / "equipment_map.yaml")
    bench, pullup = db.get(Exercise, "0001"), db.get(Exercise, "0002")
    assert bench.instructions_en == ["Lie down.", "Press."] and pullup.instructions_en is None
    set_lang("es")
    assert bench.display_name == "press de banca con mancuernas"
    assert bench.instructions == ["Acuéstate en el banco.", "Empuja las mancuernas."]
    assert catalog.muscle_label("chest") == "Pecho" and catalog.muscle_label("nope") == "nope"
    assert body.goal_label("recomp") == "Recomposición" and body.equipment_name("Casa") == "Casa"
    set_lang("en")
    assert bench.display_name == "Dumbbell Bench Press"
    assert bench.instructions == ["Lie down.", "Press."]
    assert pullup.instructions == ["Cuélgate de la barra.", "Sube hasta pasar la barbilla."]  # fallback
    assert catalog.muscle_label("chest") == "Chest" and catalog.labels(catalog.MUSCLE_ES)["lats"] == "Lats"
    assert catalog.pattern_label("hinge") == "Hip hinge" and catalog.equipment_label("ez_bar") == "EZ bar"
    assert body.goal_label("recomp") == "Recomposition" and body.labels("weekday")["mon"] == "Monday"
    assert body.equipment_name("Casa") == "Home" and body.equipment_name("Mi garaje") == "Mi garaje"
    assert body.metric_label("weight_kg") == "Weight"
    with pytest.raises(ValueError, match="value out of range for Weight"):
        body.log_metric(db, "weight_kg", 5)


def test_labs_views_follow_language():
    with session_scope() as db:
        make_catalog(db)
        demo.seed_demo(db, today=date(2026, 11, 2), weeks=2, seed=3)
    with session_scope() as db:
        prof = db.get(Profile, 1)
        set_lang("es")
        ov = labs.overview(db, prof.sex, prof.birth_date)
        assert ov["categories"][0]["name"] == "Lípidos" and ov["last_label"] == "oct 2026"
        es_ldl = labs.marker_detail(db, "ldl_c", prof.sex, prof.birth_date)
        assert es_ldl["name"] == "Colesterol LDL" and "Legumbres" in es_ldl["food_positive"]
        assert es_ldl["description"].startswith("Colesterol que viaja")
        set_lang("en")
        ov = labs.overview(db, prof.sex, prof.birth_date)
        assert ov["categories"][0] == ov["categories"][0] | {"key": "Lípidos", "name": "Lipids"}
        assert ov["last_label"] == "Oct 2026" and ov["next_label"] == "Apr 2027"
        cards = {c["key"]: c for cat in ov["categories"] for c in cat["cards"]}
        assert cards["ldl_c"]["name"] == "LDL cholesterol"
        assert {c["status_label"] for c in cards.values() if c.get("status")} <= {
            "On target", "Off target", "Borderline", "Out of range", "In range"}
        d = labs.marker_detail(db, "ldl_c", prof.sex, prof.birth_date)
        assert d["description"].startswith("Cholesterol carried in LDL")
        assert "Legumes" in d["food_positive"] and "Packaged pastries" in d["food_negative"]
        assert {row["name"] for row in d["legend"]} <= {"Standard", "Borderline", "Your target"}
        assert any(row["source"] == "NCEP ATP III (optimal)" for row in d["legend"])
        assert d["points"][-1]["lab_name"] == "Demo Lab"
        assert d["points"][-1]["label"].startswith("Oct ") and d["points"][-1]["label"].endswith(", 2026")
        nut = {i["key"]: i for i in labs.nutrition(db, prof.sex, prof.birth_date)}
        assert nut["apob"]["food_positive"] == ["Soluble fiber (oats, psyllium)", "Oily fish", "Nuts"]
        assert labs.note_text("corregido a mano · algo mío") == "corrected by hand · algo mío"
        bm = db.scalar(select(Biomarker).where(Biomarker.key == "hdl_c"))
        assert bm.display_name == "HDL cholesterol" and bm.description.startswith("Cholesterol that HDL")
