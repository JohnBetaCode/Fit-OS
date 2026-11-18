"""Exámenes: diccionario de biomarcadores, vista general, detalle de un marcador y revisión de valores.
El estado de cada punto lo calcula `ranges` con el sexo del usuario y su edad en la fecha de la toma."""

from __future__ import annotations

import logging
import math
import os
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import Biomarker, BiomarkerNote, BiomarkerRange, LabReport, LabResult
from ..core.timeutil import MONTHS_ES, age_on
from ..i18n import _, lang
from . import ranges as rg

log = logging.getLogger(__name__)

# En local: <repo>/catalog; en el contenedor (/app/fitos/labs/…) resuelve a /catalog.
CATALOG_YAML = Path(__file__).resolve().parents[3] / "catalog" / "biomarkers.yaml"
CATEGORIES = [
    "Lípidos", "Glucosa e insulina", "Hígado", "Riñón", "Hormonas", "Tiroides", "Vitaminas y minerales",
    "Inflamación y otros",
]
MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
NEXT_EXAM_MONTHS = 6
_FIELDS = {
    "name_es": None, "name_en": None, "loinc": None, "category": "Inflamación y otros", "canonical_unit": "",
    "unit_conversions": {}, "aliases": [], "direction": "range", "derived_from": None, "decimals": 1,
    "sort_order": 0, "description_es": None, "description_en": None,
}
_RANGE_FIELDS = ("kind", "sex", "age_min", "age_max", "low", "high", "label", "source")


def catalog_path() -> Path:
    return Path(os.environ.get("FITOS_BIOMARKERS_YAML") or CATALOG_YAML)


def norm(s: str) -> str:
    """Nombre normalizado para buscar alias: sin tildes, minúsculas, solo letras y dígitos."""
    s = unicodedata.normalize("NFKD", s.replace("µ", "u").replace("μ", "u"))
    return re.sub(r"[^a-z0-9]", "", s.encode("ascii", "ignore").decode().lower())


# ── Diccionario ─────────────────────────────────────────────────────────────


def ensure_biomarkers(db: Session, path: Path | str | None = None) -> int:
    """Upsert idempotente desde el YAML. Los rangos por defecto (`lab_standard`, `borderline`) se reemplazan
    salvo que el usuario haya editado ese tipo (`source='user'`); `user_target` nunca se toca."""
    p = Path(path) if path else catalog_path()
    if not p.is_file():
        log.warning("labs: no encuentro %s; diccionario sin cambios", p)
        return 0
    items = (yaml.safe_load(p.read_text("utf-8")) or {}).get("biomarkers") or []
    for item in items:
        key = item["key"]
        bm = db.get(Biomarker, key)
        if bm is None:
            bm = Biomarker(key=key)
            db.add(bm)
        for field, default in _FIELDS.items():
            setattr(bm, field, item.get(field, default))
        old = db.scalars(
            select(BiomarkerRange).where(BiomarkerRange.biomarker_key == key, BiomarkerRange.kind != "user_target")
        ).all()
        user_kinds = {r.kind for r in old if r.source == "user"}
        for r in old:
            if r.kind not in user_kinds:
                db.delete(r)
        for spec in item.get("ranges") or []:
            if spec["kind"] not in user_kinds:
                db.add(BiomarkerRange(biomarker_key=key, **{f: spec.get(f) for f in _RANGE_FIELDS if f in spec}))
    db.flush()
    return len(items)


def alias_index(db: Session) -> dict[str, str]:
    idx: dict[str, str] = {}
    for bm in db.scalars(select(Biomarker)):
        for name in [bm.key, bm.name_es, bm.name_en, *(bm.aliases or [])]:
            if name:
                idx.setdefault(norm(name), bm.key)
    return idx


def to_canonical(bm: Biomarker, unit: str | None, value: float) -> float:
    if not unit or norm(unit) == norm(bm.canonical_unit):
        return value
    for u, factor in (bm.unit_conversions or {}).items():
        if norm(u) == norm(unit):
            return value * factor
    log.warning("labs: unidad %r sin conversión a %s para %s; se usa tal cual", unit, bm.canonical_unit, bm.key)
    return value


def recompute_derived(db: Session, report: LabReport) -> list[LabResult]:
    """Calcula los marcadores derivados (p. ej. TG ÷ HDL) de un informe; borra el derivado si falta un insumo."""
    db.flush()
    rows = {r.biomarker_key: r for r in db.scalars(select(LabResult).where(LabResult.report_id == report.id))}
    out = []
    for bm in db.scalars(select(Biomarker).where(Biomarker.derived_from.is_not(None))):
        if not bm.derived_from or len(bm.derived_from) != 2:
            continue
        num, den = (_value(rows.get(k)) for k in bm.derived_from)
        cur = rows.get(bm.key)
        if num is None or not den:
            if cur is not None and cur.derived:
                db.delete(cur)
            continue
        v = round(num / den, bm.decimals)
        if cur is None:
            cur = LabResult(report_id=report.id, biomarker_key=bm.key, collected_on=report.collected_on)
            db.add(cur)
        cur.value_num = cur.value_canonical = v
        cur.unit, cur.derived, cur.raw_name = bm.canonical_unit, True, f"{bm.name_es} (calculado)"
        out.append(cur)
    db.flush()
    return out


# ── Clasificación ───────────────────────────────────────────────────────────


@dataclass
class Classified:
    value: float
    status: str
    label: str
    std: Any | None
    borderline: Any | None
    target: Any | None


def _value(r: LabResult | None) -> float | None:
    if r is None:
        return None
    return r.value_canonical if r.value_canonical is not None else r.value_num


def _age(birth: date | None, on: date) -> int | None:
    return age_on(birth, on) if birth else None


def status_text(status: str) -> str:
    """`optimal` → "En meta" / "On target", etc. (labels in `ranges.STATUS_LABELS`)."""
    return _(rg.STATUS_LABELS[status]) if status in rg.STATUS_LABELS else status


def status_label(status: str, target: Any | None) -> str:
    if status == "optimal" and not rg.has_bounds(target):
        return _("En rango")
    return status_text(status)


def category_label(category: str) -> str:
    return _(category)


def note_text(text: str | None) -> str | None:
    """A stored note in the current language: each " · "-joined part is translated if it is known copy
    (review notes, "corregido a mano"); anything the user wrote comes back as is."""
    return " · ".join(_(p) for p in text.split(" · ")) if text else text


def foods(items: list[Any] | None) -> list[str]:
    """Food guidance items in the current language (demo/known items are in the catalog; the user's own,
    imported from their notes, come back as written)."""
    return [_(str(f)) for f in items or []]


def classify(rows: list[BiomarkerRange], r: LabResult, sex: str, birth: date | None) -> Classified | None:
    """Estado de un resultado. El rango impreso por el laboratorio en ese informe manda sobre el estándar."""
    v = _value(r)
    if v is None:
        return None
    age = _age(birth, r.collected_on)
    if r.ref_low is not None or r.ref_high is not None:
        std: Any = rg.Bounds(r.ref_low, r.ref_high, r.ref_text, _("Rango impreso en el informe"))
    else:
        std = rg.select_range(rows, "lab_standard", sex, age)
    bl = rg.select_range(rows, "borderline", sex, age)
    tgt = rg.select_range(rows, "user_target", sex, age)
    st = rg.status(v, std, bl, tgt)
    return Classified(v, st, status_label(st, tgt), std, bl, tgt)


def _load(db: Session) -> tuple[list[Biomarker], dict[str, list[BiomarkerRange]], dict[str, list[LabResult]]]:
    bms = list(db.scalars(select(Biomarker).order_by(Biomarker.sort_order, Biomarker.key)))
    rngs: dict[str, list[BiomarkerRange]] = defaultdict(list)
    for r in db.scalars(select(BiomarkerRange)):
        rngs[r.biomarker_key].append(r)
    res: dict[str, list[LabResult]] = defaultdict(list)
    for r in db.scalars(select(LabResult).order_by(LabResult.collected_on, LabResult.id)):
        if _value(r) is not None:
            res[r.biomarker_key].append(r)
    return bms, rngs, res


def month_label(d: date, precision: str = "month") -> str:
    """"oct 2026" / "14 oct 2026"; in English "Oct 2026" / "Oct 14, 2026"."""
    if lang() == "en":
        m = MONTHS_EN[d.month - 1]
        return f"{m} {d.year}" if precision == "month" else f"{m} {d.day}, {d.year}"
    m = MONTHS_ES[d.month - 1]
    return f"{m} {d.year}" if precision == "month" else f"{d.day} {m} {d.year}"


def add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, min(d.day, 28))


def _spark(values: list[float], w: int = 96, h: int = 28, pad: int = 4) -> dict[str, Any]:
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    step = (w - 2 * pad) / max(len(values) - 1, 1)
    pts = [
        (round(pad + i * step if len(values) > 1 else w / 2, 1), round(h - pad - (v - lo) / span * (h - 2 * pad), 1))
        for i, v in enumerate(values)
    ]
    if hi == lo:
        pts = [(x, h / 2) for x, _ in pts]
    return {"points": " ".join(f"{x},{y}" for x, y in pts), "last": pts[-1], "w": w, "h": h}


# ── Vista general ───────────────────────────────────────────────────────────


def overview(db: Session, sex: str, birth_date: date | None) -> dict[str, Any]:
    bms, rngs, res = _load(db)
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    counts = {"markers": 0, "out_of_target": 0, "out_of_range": 0, "borderline": 0, "needs_review": 0}
    for bm in bms:
        rows = res.get(bm.key, [])
        card: dict[str, Any] = {"key": bm.key, "name": bm.display_name, "unit": bm.canonical_unit,
                                "decimals": bm.decimals}
        if rows:
            cls = [classify(rngs[bm.key], r, sex, birth_date) for r in rows]
            last, c = rows[-1], cls[-1]
            assert c is not None
            prev = cls[-2].value if len(cls) > 1 and cls[-2] else None
            delta = round(c.value - prev, bm.decimals) if prev is not None else None
            card |= {
                "value": c.value, "status": c.status, "status_label": c.label, "date": month_label(last.collected_on),
                "delta": delta, "delta_tone": rg.delta_tone(delta, bm.direction),
                "target_text": rg.text_of(c.target) if rg.has_bounds(c.target) else None,
                "std_text": rg.text_of(c.std) if rg.has_bounds(c.std) else None,
                "needs_review": last.needs_review, "derived": last.derived,
                "spark": _spark([x.value for x in cls if x]), "n": len(rows),
            }
            counts["markers"] += 1
            counts["out_of_target"] += rg.meets_target(c.value, c.target) is False
            counts["out_of_range"] += c.status == "out_of_range"
            counts["borderline"] += c.status == "borderline"
        groups[bm.category].append(card)
    pending = review_queue(db)
    counts["needs_review"] = len(pending)
    last = db.scalar(select(LabReport.collected_on).order_by(LabReport.collected_on.desc()).limit(1))
    cats = [{"key": c, "name": category_label(c), "cards": groups.pop(c)} for c in CATEGORIES if c in groups]
    cats += [{"key": c, "name": category_label(c), "cards": cards} for c, cards in groups.items()]
    return {
        "categories": cats,
        "counts": counts,
        "last_date": last,
        "last_label": month_label(last) if last else None,
        "next_label": month_label(add_months(last, NEXT_EXAM_MONTHS)) if last else None,
        "pending": pending,
    }


def review_queue(db: Session) -> list[dict[str, Any]]:
    out = []
    q = (
        select(LabResult, Biomarker)
        .join(Biomarker, Biomarker.key == LabResult.biomarker_key)
        .where(LabResult.needs_review.is_(True))
        .order_by(LabResult.collected_on)
    )
    for r, bm in db.execute(q):
        out.append({
            "id": r.id, "key": bm.key, "name": bm.display_name, "value": _value(r), "unit": bm.canonical_unit,
            "decimals": bm.decimals, "date": month_label(r.collected_on), "note": note_text(r.note),
        })
    return out


def nutrition(db: Session, sex: str, birth_date: date | None) -> list[dict[str, Any]]:
    """Pautas de alimentos de los marcadores cuyo último valor no cumple la meta o está fuera de rango."""
    ov = overview(db, sex, birth_date)
    notes = {n.biomarker_key: n for n in db.scalars(select(BiomarkerNote))}
    out = []
    for cat in ov["categories"]:
        for card in cat["cards"]:
            n = notes.get(card["key"])
            if card.get("status") in ("in_range", "borderline", "out_of_range") and n is not None:
                out.append(card | {"food_positive": foods(n.food_positive), "food_negative": foods(n.food_negative)})
    return out


# ── Detalle ─────────────────────────────────────────────────────────────────


def _nice_extent(lo: float, hi: float) -> tuple[float, float]:
    span = hi - lo or abs(hi) or 1.0
    raw = span / 4
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    y0 = (lo - span * 0.08) // step * step
    y1 = -((-(hi + span * 0.08)) // step) * step
    if lo >= 0:
        y0 = max(0.0, y0)
    return round(y0, 6), round(y1, 6)


def _segments(dates: list[date], bounds: list[Any | None], pad_days: int) -> list[dict[str, Any]]:
    """Bandas por tramos: cada punto ocupa del punto medio con el anterior al punto medio con el siguiente, y
    tramos contiguos con el mismo rango se funden (la banda cambia si el usuario cambió de banda de edad)."""
    segs: list[dict[str, Any]] = []
    for i, (d, b) in enumerate(zip(dates, bounds, strict=True)):
        start = dates[i - 1] + (d - dates[i - 1]) / 2 if i else d - timedelta(days=pad_days)
        end = d + (dates[i + 1] - d) / 2 if i + 1 < len(dates) else d + timedelta(days=pad_days)
        if not rg.has_bounds(b):
            continue
        if segs and segs[-1]["_b"] == (b.low, b.high) and segs[-1]["end"] == start.isoformat():
            segs[-1]["end"] = end.isoformat()
        else:
            segs.append({"_b": (b.low, b.high), "start": start.isoformat(), "end": end.isoformat(),
                         "low": b.low, "high": b.high,
                         "text": b.label if isinstance(b, rg.Bounds) and rg.is_range_label(b.label) else rg.text_of(b)})
    for s in segs:
        s.pop("_b")
    return segs


def _legend_row(kind: str, r: Any) -> dict[str, Any]:
    who = []
    if getattr(r, "sex", "any") in ("male", "female"):
        who.append(_("hombres") if r.sex == "male" else _("mujeres"))
    amin, amax = getattr(r, "age_min", None), getattr(r, "age_max", None)
    if amin is not None or amax is not None:
        who.append(_("{a}–{b} años", a=amin or 0, b=amax) if amax is not None else _("≥ {a} años", a=amin))
    names = {"lab_standard": _("Estándar"), "borderline": _("Limítrofe"), "user_target": _("Tu meta")}
    label = _(r.label) if kind != "lab_standard" and r.label and not rg.is_range_label(r.label) else None
    source = getattr(r, "source", None)
    return {"kind": kind, "name": names[kind], "text": rg.text_of(r), "label": label, "who": ", ".join(who),
            "source": _(source) if source else source}


def marker_detail(db: Session, key: str, sex: str, birth_date: date | None) -> dict[str, Any] | None:
    bm = db.get(Biomarker, key)
    if bm is None:
        return None
    rows = list(db.scalars(select(BiomarkerRange).where(BiomarkerRange.biomarker_key == key)))
    results = [
        r for r in db.scalars(
            select(LabResult).where(LabResult.biomarker_key == key).order_by(LabResult.collected_on, LabResult.id)
        ) if _value(r) is not None
    ]
    reports = {r.id: r for r in db.scalars(select(LabReport).where(LabReport.id.in_([x.report_id for x in results])))}
    points, cls = [], []
    for r in results:
        c = classify(rows, r, sex, birth_date)
        assert c is not None
        cls.append(c)
        rep = reports.get(r.report_id)
        points.append({
            "id": r.id, "date": r.collected_on.isoformat(),
            "label": month_label(r.collected_on, rep.date_precision if rep else "month"),
            "value": c.value, "comparator": r.comparator, "status": c.status, "status_label": c.label,
            "std_text": rg.text_of(c.std) if rg.has_bounds(c.std) else "—",
            "ref_text": r.ref_text or (rg.range_text(r.ref_low, r.ref_high) if r.ref_low or r.ref_high else None),
            "target_ok": rg.meets_target(c.value, c.target), "needs_review": r.needs_review, "derived": r.derived,
            "report_id": r.report_id, "lab_name": _(rep.lab_name) if rep and rep.lab_name else None,
        })
    today_age = _age(birth_date, results[-1].collected_on if results else date.today())
    target = rg.select_range(rows, "user_target", sex, today_age)
    std_now = cls[-1].std if cls else rg.select_range(rows, "lab_standard", sex, today_age)
    dates = [r.collected_on for r in results]
    std_segs = _segments(dates, [c.std for c in cls], 25)
    # La banda limítrofe se dibuja pegada al estándar, igual que se clasifica.
    bl_segs = _segments(dates, [rg.borderline_zone(c.std, c.borderline) if c.borderline else None for c in cls], 25)
    # Meta y estándar iguales en todo el periodo → se dibuja solo el estándar.
    show_target = rg.has_bounds(target) and not (cls and all(rg.same_bounds(target, c.std) for c in cls))
    edges = [c.value for c in cls]
    for s in std_segs + bl_segs:
        edges += [v for v in (s["low"], s["high"]) if v is not None]
    if show_target:
        edges += [v for v in (target.low, target.high) if v is not None]
    y0, y1 = _nice_extent(min(edges), max(edges)) if edges else (0, 1)
    for s in std_segs + bl_segs:
        s["low"] = y0 if s["low"] is None else s["low"]
        s["high"] = y1 if s["high"] is None else s["high"]
    legend = []
    seen = set()
    for kind, r in [("lab_standard", c.std) for c in cls] + [("borderline", c.borderline) for c in cls]:
        if rg.has_bounds(r) and (kind, r.low, r.high, getattr(r, "source", None)) not in seen:
            seen.add((kind, r.low, r.high, getattr(r, "source", None)))
            legend.append(_legend_row(kind, r))
    if not cls:
        legend += [_legend_row(r.kind, r) for r in rows if r.kind != "user_target"]
    if rg.has_bounds(target):
        legend.append(_legend_row("user_target", target) | {"drawn": show_target})
    note = db.get(BiomarkerNote, key)
    counts = {
        "out_of_target": sum(p["target_ok"] is False for p in points),
        "out_of_range": sum(c.status == "out_of_range" for c in cls),
        "borderline": sum(c.status == "borderline" for c in cls),
        "needs_review": sum(p["needs_review"] for p in points),
    }
    last = points[-1] if points else None
    chart = {
        "unit": bm.canonical_unit, "decimals": bm.decimals, "y": [y0, y1],
        "x": [(dates[0] - timedelta(days=25)).isoformat(), (dates[-1] + timedelta(days=25)).isoformat()]
        if dates else None,
        "points": [{k: p[k] for k in ("date", "label", "value", "status", "status_label", "needs_review")}
                   for p in points],
        "std": std_segs, "borderline": bl_segs,
        "target": {"low": target.low, "high": target.high, "text": rg.text_of(target),
                   "label": None if not target.label or rg.is_range_label(target.label) else _(target.label)}
        if show_target else None,
    }
    return {
        "bm": bm, "name": bm.display_name, "description": bm.description, "points": points, "last": last, "counts": counts, "legend": legend, "chart": chart,
        "std_now_text": rg.text_of(std_now) if rg.has_bounds(std_now) else None,
        "target_text": rg.text_of(target) if rg.has_bounds(target) else None,
        "target_label": _(target.label)
        if rg.has_bounds(target) and target.label and not rg.is_range_label(target.label) else None,
        "has_ref": any(p["ref_text"] for p in points),
        "food_positive": foods(note.food_positive) if note else [],
        "food_negative": foods(note.food_negative) if note else [],
        "note_raw": _(note.raw) if note and note.raw else None,
    }


# ── Revisión ────────────────────────────────────────────────────────────────


def confirm_result(db: Session, result_id: int) -> LabResult | None:
    r = db.get(LabResult, result_id)
    if r is not None:
        r.needs_review = False
        db.flush()
    return r


def correct_result(db: Session, result_id: int, value: float, key: str | None = None) -> LabResult | None:
    """Corrige valor y, si hace falta, el marcador; queda confirmado. Recalcula los derivados del informe."""
    r = db.get(LabResult, result_id)
    if r is None:
        return None
    key = key or r.biomarker_key
    bm = db.get(Biomarker, key)
    if bm is None:
        raise ValueError(_("marcador desconocido: {key}", key=key))
    if key != r.biomarker_key and db.scalar(
        select(LabResult.id).where(LabResult.report_id == r.report_id, LabResult.biomarker_key == key)
    ):
        raise ValueError(_("ese informe ya tiene un valor de {name}", name=bm.display_name))
    if key != r.biomarker_key:
        r.biomarker_key, r.unit = key, bm.canonical_unit
    r.value_num, r.needs_review = value, False
    r.value_canonical = to_canonical(bm, r.unit, value)
    r.note = ((r.note + " · ") if r.note else "") + "corregido a mano"
    db.flush()
    recompute_derived(db, r.report)
    return r
