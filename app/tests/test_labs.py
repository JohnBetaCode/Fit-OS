"""Exámenes: selección de rangos, estado, semilla, vista general y páginas."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace as R

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from fitos.core.db import session_scope
from fitos.core.models import Biomarker, BiomarkerNote, BiomarkerRange, LabReport, LabResult, Profile
from fitos.labs import ranges as rg
from fitos.labs import service
from fitos.labs.seed_import import import_seed_csv, parse_cell, split_notes
from fitos.web.routes_labs import router

FIXTURE = Path(__file__).parent / "fixtures" / "labs_seed_fixture.csv"
BIRTH = date(1990, 3, 15)


def rng(kind="lab_standard", sex="any", age_min=None, age_max=None, low=None, high=None):
    return R(kind=kind, sex=sex, age_min=age_min, age_max=age_max, low=low, high=high, label=None, source=None)


@pytest.fixture
def seeded(db):
    service.ensure_biomarkers(db)
    db.add(Profile(id=1, sex="male", birth_date=BIRTH))
    rep = import_seed_csv(db, FIXTURE)
    db.commit()
    return rep


# ── ranges ─────────────────────────────────────────────────────────────────


def test_select_range_prefers_exact_sex_and_narrow_band():
    rows = [
        rng(sex="any", low=1),
        rng(sex="male", age_min=20, age_max=49, low=249, high=836),
        rng(sex="male", age_min=50, low=193, high=740),
        rng(kind="borderline", sex="male", low=0),
    ]
    assert rg.select_range(rows, "lab_standard", "male", 35).low == 249
    assert rg.select_range(rows, "lab_standard", "male", 61).low == 193
    assert rg.select_range(rows, "lab_standard", "female", 35).low == 1
    assert rg.select_range(rows, "lab_standard", "male", 15).low == 1  # ninguna banda de hombre aplica
    assert rg.select_range(rows, "lab_standard", "male", None).low == 193  # edad desconocida: la más ancha
    assert rg.select_range(rows, "user_target", "male", 35) is None
    narrow = [rng(age_min=0, age_max=99, low=1), rng(age_min=30, age_max=39, low=2)]
    assert rg.select_range(narrow, "lab_standard", "male", 33).low == 2


def test_select_range_from_catalog_by_sex(db):
    service.ensure_biomarkers(db)
    rows = db.scalars(select(BiomarkerRange).where(BiomarkerRange.biomarker_key == "hdl_c")).all()
    assert rg.select_range(rows, "lab_standard", "male", 36).low == 40
    assert rg.select_range(rows, "lab_standard", "female", 36).low == 50


@pytest.mark.parametrize(
    ("value", "expected"),
    [(65, "optimal"), (99.6, "in_range"), (100, "borderline"), (120, "borderline"), (129, "borderline"),
     (140, "out_of_range")],
)
def test_status_ldl(value, expected):
    std, bl, tgt = rng(high=100), rng("borderline", low=100, high=129), rng("user_target", high=70)
    assert rg.status(value, std, bl, tgt) == expected


def test_status_edges():
    glucose = rng(low=70, high=99)
    assert rg.status(65, glucose, rng("borderline", low=100, high=125), rng("user_target", high=90)) == "out_of_range"
    assert rg.status(85, glucose, None, None) == "optimal"  # sin meta, en rango = óptimo
    assert rg.status(150, None, None, rng("user_target", high=140)) == "in_range"  # sin estándar: solo la meta
    vit_d = rng(low=30, high=100)
    assert rg.status(29.5, vit_d, rng("borderline", low=20, high=29)) == "borderline"  # sin hueco 29–30
    assert rg.status(45, rng(low=40), None, rng("user_target", low=50)) == "in_range"


def test_range_text():
    assert rg.range_text(None, 100) == "< 100"
    assert rg.range_text(70, 99) == "70–99"
    assert rg.range_text(40, None) == "≥ 40"
    assert rg.range_text(0.27, 4.2) == "0.27–4.2"
    assert rg.range_text(None, None) == "—"


def test_parse_cell_and_notes():
    assert parse_cell("0.91-CR") is None
    assert parse_cell(" 5,4 ") == (None, 5.4)
    assert parse_cell("<0.5") == ("<", 0.5)
    pos, neg = split_notes("Food positive: oats, avocado; Food negative: fried food, processed meat")
    assert pos == ["oats", "avocado"] and neg == ["fried food", "processed meat"]


# ── catálogo y semilla ─────────────────────────────────────────────────────


def test_ensure_biomarkers_idempotent_and_keeps_targets(db):
    assert service.ensure_biomarkers(db) == 29
    db.add(BiomarkerRange(biomarker_key="ldl_c", kind="user_target", sex="any", high=70, source="user"))
    db.flush()
    n_ranges = db.scalar(select(func.count()).select_from(BiomarkerRange))
    service.ensure_biomarkers(db)
    assert db.scalar(select(func.count()).select_from(Biomarker)) == 29
    assert db.scalar(select(func.count()).select_from(BiomarkerRange)) == n_ranges
    assert db.get(Biomarker, "tg_hdl_ratio").derived_from == ["triglycerides", "hdl_c"]


def test_seed_import_counts(seeded, db, caplog):
    assert (seeded.reports, seeded.measured, seeded.derived, seeded.needs_review) == (6, 125, 6, 1)
    assert seeded.unmapped == []
    assert seeded.skipped_cells == ["Cystatin C 2026-10"]
    assert seeded.ratio_mismatches == ["tg_hdl_ratio 2026-10"]
    reps = db.scalars(select(LabReport)).all()
    assert {(r.source, r.date_precision, r.collected_on.day) for r in reps} == {("seed_csv", "month", 1)}
    cr = db.scalars(select(LabResult).where(LabResult.biomarker_key == "creatinine")).one()
    assert cr.needs_review and cr.collected_on == date(2026, 10, 1)
    assert not db.scalars(
        select(LabResult).where(LabResult.biomarker_key == "cystatin_c", LabResult.collected_on == date(2026, 10, 1))
    ).all()
    ratio = db.scalars(select(LabResult).where(LabResult.biomarker_key == "tg_hdl_ratio").order_by(
        LabResult.collected_on)).all()
    assert all(r.derived for r in ratio) and ratio[0].value_num == pytest.approx(2.73)
    tgt = db.scalars(select(BiomarkerRange).where(
        BiomarkerRange.biomarker_key == "glucose_ogtt_120", BiomarkerRange.kind == "user_target")).one()
    assert (tgt.high, tgt.label) == (100, "meta (90′)")
    note = db.get(BiomarkerNote, "triglycerides")
    assert note.food_positive == ["fatty fish", "walnuts"]
    assert note.food_negative == ["sugary drinks", "white bread", "alcohol"]


def test_seed_import_idempotent_and_keeps_confirmation(seeded, db, caplog):
    cr = db.scalars(select(LabResult).where(LabResult.needs_review.is_(True))).one()
    service.confirm_result(db, cr.id)
    caplog.set_level(logging.WARNING)
    again = import_seed_csv(db, FIXTURE)
    assert (again.reports, again.measured, again.derived, again.needs_review) == (6, 125, 6, 0)
    assert db.scalar(select(func.count()).select_from(BiomarkerRange).where(
        BiomarkerRange.kind == "user_target")) == 29
    assert "0.91" not in caplog.text and "104" not in caplog.text  # nunca valores en los logs


def test_overview_counts(seeded, db):
    ov = service.overview(db, "male", BIRTH)
    assert ov["counts"] == {"markers": 29, "out_of_target": 4, "out_of_range": 1, "borderline": 1,
                            "needs_review": 1}
    assert [c["name"] for c in ov["categories"]][:2] == ["Lípidos", "Glucosa e insulina"]
    cards = {k["key"]: k for c in ov["categories"] for k in c["cards"]}
    assert cards["glucose_fasting"]["status"] == "borderline"
    assert (cards["glucose_fasting"]["delta"], cards["glucose_fasting"]["delta_tone"]) == (5, "bad")
    assert cards["hs_crp"]["status"] == "out_of_range"
    assert cards["ldl_c"]["status"] == "in_range" and cards["ldl_c"]["target_text"] == "< 70"
    assert cards["creatinine"]["needs_review"]
    assert ov["last_label"] == "oct 2026" and ov["next_label"] == "abr 2027"
    assert {i["key"] for i in service.nutrition(db, "male", BIRTH)} == {"ldl_c", "glucose_fasting"}


def test_marker_detail_bands_and_target(seeded, db):
    d = service.marker_detail(db, "ldl_c", "male", BIRTH)
    ch = d["chart"]
    assert len(ch["points"]) == 6 and ch["target"]["high"] == 70
    assert [(s["low"], s["high"]) for s in ch["std"]] == [(ch["y"][0], 100)]  # una banda, fundida
    assert ch["borderline"][0]["high"] == 129 and ch["y"][1] >= 129
    assert d["counts"]["out_of_target"] == 6
    assert d["food_positive"] == ["oats", "avocado", "olive oil"]
    hba1c = service.marker_detail(db, "hba1c", "male", BIRTH)
    assert hba1c["chart"]["target"] is None  # meta = estándar: se dibuja uno solo


def test_marker_detail_std_changes_with_age(seeded, db):
    d = service.marker_detail(db, "testosterone_total", "male", date(1975, 6, 1))  # cumple 50 en 2025
    assert [s["low"] for s in d["chart"]["std"]] == [249, 193]


def test_correct_result_moves_marker_and_recomputes(seeded, db):
    cr = db.scalars(select(LabResult).where(LabResult.needs_review.is_(True))).one()
    r = service.correct_result(db, cr.id, 0.95, "creatinine")
    assert not r.needs_review and r.value_canonical == 0.95
    hdl = db.scalars(select(LabResult).where(
        LabResult.biomarker_key == "hdl_c", LabResult.collected_on == date(2026, 10, 1))).one()
    service.correct_result(db, hdl.id, 46)
    ratio = db.scalars(select(LabResult).where(
        LabResult.biomarker_key == "tg_hdl_ratio", LabResult.collected_on == date(2026, 10, 1))).one()
    assert ratio.value_num == pytest.approx(2.0)
    with pytest.raises(ValueError):
        service.correct_result(db, hdl.id, 46, "ldl_c")  # ese informe ya tiene LDL


# ── web ────────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        yield c


def test_pages_render(seeded, client):
    r = client.get("/examenes")
    assert r.status_code == 200
    assert "1 valor por confirmar" in r.text and "Glucosa en ayunas" in r.text
    r = client.get("/examenes/ldl_c")
    assert r.status_code == 200 and "labs-chart-data" in r.text and "olive oil" in r.text
    assert client.get("/examenes/nutricion").status_code == 200
    assert client.get("/examenes/glucose_ogtt_30").status_code == 200
    assert client.get("/examenes/nope").status_code == 404


def test_pages_render_empty(client):
    with session_scope() as db:
        service.ensure_biomarkers(db)
    assert client.get("/examenes").status_code == 200
    assert client.get("/examenes/ldl_c").status_code == 200


def test_confirm_via_htmx(seeded, client):
    with session_scope() as db:
        rid = db.scalars(select(LabResult.id).where(LabResult.needs_review.is_(True))).one()
    r = client.post(f"/examenes/resultados/{rid}/confirmar", headers={"HX-Request": "true"})
    assert r.status_code == 200 and r.headers["HX-Refresh"] == "true"
    r = client.post(f"/examenes/resultados/{rid}/corregir", data={"value": "0,93", "key": "creatinine"},
                    follow_redirects=False)
    assert r.status_code == 303
    with session_scope() as db:
        assert db.get(LabResult, rid).value_num == 0.93
        assert service.overview(db, "male", BIRTH)["counts"]["needs_review"] == 0
