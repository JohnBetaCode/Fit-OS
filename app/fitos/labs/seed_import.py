"""Importación de la semilla de exámenes exportada de Notion (docs/design.md §9.2). Idempotente.

Formato: `marker,unit,range_text,low,high,<YYYY-MM>…,notes`. Cada columna de mes es un informe
(`date_precision='month'`, día 1). `low`/`high` son la meta del usuario. Nunca se loguean valores."""

from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.models import Biomarker, BiomarkerNote, BiomarkerRange, LabReport, LabResult
from .service import alias_index, ensure_biomarkers, norm, recompute_derived, to_canonical

log = logging.getLogger(__name__)

SOURCE = "seed_csv"
RATIO_TOLERANCE = 0.06
# Nombres de la exportación de Notion → key. Lo que no esté aquí se busca en los alias del diccionario.
SEED_NAMES = {norm(k): v for k, v in {
    "LDL": "ldl_c", "LDL-C": "ldl_c", "HDL": "hdl_c", "HDL-C": "hdl_c", "Triglycerides": "triglycerides",
    "TG/HDL": "tg_hdl_ratio", "TG/HDL ratio": "tg_hdl_ratio", "TG/HDL-C ratio": "tg_hdl_ratio", "Total cholesterol": "total_cholesterol",
    "ApoB": "apob", "Homocysteine": "homocysteine", "hs-CRP": "hs_crp", "CRP": "hs_crp",
    "Fasting insulin": "insulin_fasting", "Insulin": "insulin_fasting", "Fasting glucose": "glucose_fasting",
    "Glucose": "glucose_fasting", "OGTT 30": "glucose_ogtt_30", "OGTT 60": "glucose_ogtt_60",
    "OGTT 120": "glucose_ogtt_120", "HbA1c": "hba1c", "ALT": "alt", "AST": "ast", "Cystatin C": "cystatin_c",
    "Creatinine": "creatinine", "Vitamin D": "vitamin_d_25oh", "Vitamin B12": "vitamin_b12", "TSH": "tsh",
    "Fibrinogen": "fibrinogen", "Uric acid": "uric_acid", "Ferritin": "ferritin", "Estradiol": "estradiol",
    "FSH": "fsh", "LH": "lh", "Testosterone": "testosterone_total", "Total testosterone": "testosterone_total",
    "Free testosterone": "testosterone_free",
}.items()}
LAB_NAME = "Notion (semilla)"
# Ambigüedades conocidas de la semilla. Stored Spanish texts (note, labels, lab name) are translated on display
# (`service.note_text`, `_()`); their English is in `i18n/en_data.py`.
REVIEW = {"creatinine": "Viene de la celda con sufijo «-CR» de la fila de cistatina C; confirmar el marcador."}
TARGET_LABELS = {"glucose_ogtt_120": "meta (90′)"}

_MONTH = re.compile(r"^\d{4}-\d{2}$")
_CELL = re.compile(r"^\s*(<=|>=|≤|≥|<|>)?\s*(-?\d+(?:[.,]\d+)?)\s*$")
_FOOD = re.compile(r"food\s+(positive|negative)\s*[:\-–]?\s*", re.I)


@dataclass
class SeedReport:
    reports: int = 0
    measured: int = 0
    derived: int = 0
    needs_review: int = 0
    unmapped: list[str] = field(default_factory=list)
    skipped_cells: list[str] = field(default_factory=list)
    ratio_mismatches: list[str] = field(default_factory=list)


def parse_cell(cell: str | None) -> tuple[str | None, float] | None:
    """`(comparador, valor)` o None si la celda está vacía o no es un número limpio (p. ej. "0.87-CR")."""
    m = _CELL.match(cell or "")
    if not m:
        return None
    comp = {"<=": "<=", "≤": "<=", ">=": ">=", "≥": ">="}.get(m[1] or "", m[1])
    return comp, float(m[2].replace(",", "."))


def _num(s: str | None) -> float | None:
    p = parse_cell(s)
    return p[1] if p else None


def split_notes(raw: str) -> tuple[list[str], list[str]]:
    """Separa "Food positive: …" y "Food negative: …" en listas de alimentos."""
    parts = _FOOD.split(raw or "")
    out: dict[str, list[str]] = {"positive": [], "negative": []}
    for kind, text in zip(parts[1::2], parts[2::2], strict=True):
        out[kind.lower()] += [i for i in (x.strip(" .;:-–\t") for x in re.split(r"[,;\n•|]", text)) if i]
    return out["positive"], out["negative"]


def _report(db: Session, col: str) -> LabReport:
    day = date(int(col[:4]), int(col[5:7]), 1)
    rep = db.scalar(select(LabReport).where(LabReport.source == SOURCE, LabReport.collected_on == day))
    if rep is None:
        rep = LabReport(collected_on=day, date_precision="month", source=SOURCE, lab_name=LAB_NAME)
        db.add(rep)
        db.flush()
    return rep


def _target(db: Session, key: str, row: dict[str, str]) -> None:
    low, high = _num(row.get("low")), _num(row.get("high"))
    old = db.scalars(
        select(BiomarkerRange).where(BiomarkerRange.biomarker_key == key, BiomarkerRange.kind == "user_target")
    ).all()
    if any(r.source != SOURCE for r in old):  # el usuario ya la editó
        return
    for r in old:
        db.delete(r)
    if low is not None or high is not None:
        label = TARGET_LABELS.get(key) or (row.get("range_text") or "").strip()[:64] or None
        db.add(BiomarkerRange(biomarker_key=key, kind="user_target", sex="any", low=low, high=high,
                              label=label, source=SOURCE))


def _notes(db: Session, key: str, raw: str) -> None:
    raw = (raw or "").strip()
    if not raw:
        return
    pos, neg = split_notes(raw)
    note = db.get(BiomarkerNote, key) or BiomarkerNote(biomarker_key=key)
    note.food_positive, note.food_negative, note.raw = pos, neg, raw
    db.add(note)


def import_seed_csv(db: Session, path: Path | str) -> SeedReport:
    if db.scalar(select(func.count()).select_from(Biomarker)) == 0:
        ensure_biomarkers(db)
    out = SeedReport()
    idx = alias_index(db)
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        months = [c for c in reader.fieldnames or [] if _MONTH.match(c.strip())]
        reports = {c: _report(db, c.strip()) for c in months}
        csv_ratio: dict[str, dict[str, float | None]] = {}
        for row in reader:
            name = (row.get("marker") or "").strip()
            if not name:
                continue
            key = SEED_NAMES.get(norm(name)) or idx.get(norm(name))
            bm = db.get(Biomarker, key) if key else None
            if bm is None:
                out.unmapped.append(name)
                continue
            _target(db, bm.key, row)
            _notes(db, bm.key, row.get("notes") or "")
            if bm.derived_from:  # se calcula por código; el CSV solo sirve para comprobar
                csv_ratio[bm.key] = {c: _num(row.get(c)) for c in months}
                continue
            unit = (row.get("unit") or "").strip() or bm.canonical_unit
            for col in months:
                cell = (row.get(col) or "").strip()
                if not cell:
                    continue
                parsed = parse_cell(cell)
                if parsed is None:
                    out.skipped_cells.append(f"{name} {col}")
                    log.warning("labs seed: celda no numérica en %s %s; omitida", name, col)
                    continue
                rep = reports[col]
                exists = db.scalar(
                    select(LabResult.id).where(LabResult.report_id == rep.id, LabResult.biomarker_key == bm.key)
                )
                if exists:  # idempotente y respeta confirmaciones/correcciones previas
                    continue
                comp, v = parsed
                db.add(LabResult(
                    report_id=rep.id, biomarker_key=bm.key, collected_on=rep.collected_on, value_num=v,
                    comparator=comp, unit=unit, value_canonical=to_canonical(bm, unit, v), raw_name=name,
                    needs_review=bm.key in REVIEW, note=REVIEW.get(bm.key),
                ))
        db.flush()
        for col, rep in reports.items():
            for d in recompute_derived(db, rep):
                expected = csv_ratio.get(d.biomarker_key, {}).get(col)
                if expected is not None and abs(expected - (d.value_num or 0)) > RATIO_TOLERANCE:
                    out.ratio_mismatches.append(f"{d.biomarker_key} {col}")
                    log.warning("labs seed: %s %s calculado difiere del CSV en más de %s",
                                d.biomarker_key, col, RATIO_TOLERANCE)
    ids = [r.id for r in reports.values()]
    base = select(func.count()).select_from(LabResult).where(LabResult.report_id.in_(ids))
    out.reports = len(ids)
    out.measured = db.scalar(base.where(LabResult.derived.is_(False))) or 0
    out.derived = db.scalar(base.where(LabResult.derived.is_(True))) or 0
    out.needs_review = db.scalar(base.where(LabResult.needs_review.is_(True))) or 0
    return out
