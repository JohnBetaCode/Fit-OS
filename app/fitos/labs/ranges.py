"""Rangos de biomarcadores y estado de cada valor. Funciones puras: reciben filas (ORM o cualquier objeto con
`kind`, `sex`, `age_min`, `age_max`, `low`, `high`) y no tocan la base.

Convención de límites (la misma del CSV y de las guías):
- solo `high` → `v < high` ("< 100");
- solo `low` → `v ≥ low` ("≥ 40");
- ambos → `low ≤ v ≤ high` ("70–99").
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

STATUSES = ("optimal", "in_range", "borderline", "out_of_range")
STATUS_LABELS = {
    "optimal": "En meta",
    "in_range": "Fuera de meta",
    "borderline": "Limítrofe",
    "out_of_range": "Fuera de rango",
}


class RangeLike(Protocol):
    low: float | None
    high: float | None


@dataclass(frozen=True)
class Bounds:
    """Rango suelto (p. ej. el impreso por el laboratorio en un informe)."""

    low: float | None = None
    high: float | None = None
    label: str | None = None
    source: str | None = None


def _width(r: Any) -> float:
    lo = r.age_min if r.age_min is not None else 0
    hi = r.age_max if r.age_max is not None else 200
    return hi - lo


def select_range(ranges: Iterable[Any], kind: str, sex: str | None, age: int | None) -> Any | None:
    """La fila más específica de `kind`: sexo exacto antes que `any`, banda de edad más estrecha antes que la
    más ancha. Con edad desconocida no se filtra por edad y gana la banda más ancha."""
    cands = []
    for r in ranges:
        if r.kind != kind or r.sex not in (sex, "any"):
            continue
        if age is not None and not (
            (r.age_min is None or age >= r.age_min) and (r.age_max is None or age <= r.age_max)
        ):
            continue
        cands.append(r)
    if not cands:
        return None
    if age is None:
        return min(cands, key=lambda r: (r.sex != sex, -_width(r)))
    return min(cands, key=lambda r: (r.sex != sex, _width(r)))


def contains(r: RangeLike | None, v: float) -> bool:
    if r is None or (r.low is None and r.high is None):
        return False
    if r.low is not None and r.high is not None:
        return r.low <= v <= r.high
    if r.high is not None:
        return v < r.high
    return v >= r.low  # type: ignore[operator]


def borderline_zone(std: RangeLike | None, b: RangeLike) -> Bounds:
    """La zona limítrofe se estira hasta tocar el estándar: con "70–99" y "100–125", 99,6 no cae en un hueco."""
    lo, hi = b.low, b.high
    if std is not None:
        if std.high is not None and lo is not None and lo >= std.high:
            lo = std.high
        if std.low is not None and hi is not None and hi <= std.low:
            hi = std.low
    return Bounds(lo, hi, label=text_of(b))


def has_bounds(r: RangeLike | None) -> bool:
    return r is not None and (r.low is not None or r.high is not None)


def status(
    value: float,
    std: RangeLike | None,
    borderline: RangeLike | None = None,
    target: RangeLike | None = None,
) -> str:
    """`out_of_range` | `borderline` | `optimal` | `in_range`.

    Fuera del estándar manda el estándar (limítrofe si cae en esa zona). Dentro del estándar (o sin estándar),
    `optimal` si cumple la meta del usuario e `in_range` si no; sin meta, dentro del estándar es `optimal`."""
    if has_bounds(std) and not contains(std, value):
        if has_bounds(borderline) and contains(borderline_zone(std, borderline), value):  # type: ignore[arg-type]
            return "borderline"
        return "out_of_range"
    if has_bounds(target):
        return "optimal" if contains(target, value) else "in_range"
    return "optimal"


def meets_target(value: float, target: RangeLike | None) -> bool | None:
    """None si no hay meta."""
    return contains(target, value) if has_bounds(target) else None


def fmt_bound(v: float) -> str:
    return f"{v:g}"


def range_text(low: float | None, high: float | None) -> str:
    """"< 100", "≥ 40", "70–99"; "—" sin límites."""
    if low is not None and high is not None:
        return f"{fmt_bound(low)}–{fmt_bound(high)}"
    if high is not None:
        return f"< {fmt_bound(high)}"
    if low is not None:
        return f"≥ {fmt_bound(low)}"
    return "—"


def text_of(r: RangeLike | None) -> str:
    return range_text(r.low, r.high) if r is not None else "—"


def is_range_label(label: str | None) -> bool:
    """True si la etiqueta solo repite el rango ("<70", "30-80"); False si aporta algo ("meta (90′)")."""
    return bool(label) and label.lstrip()[:1] in "<>≤≥0123456789"  # type: ignore[union-attr]


def same_bounds(a: RangeLike | None, b: RangeLike | None) -> bool:
    return a is not None and b is not None and (a.low, a.high) == (b.low, b.high)


def delta_tone(delta: float | None, direction: str) -> str:
    """`good` | `bad` | `neutral` según hacia dónde es mejor el marcador (`lower` | `higher` | `range`)."""
    if not delta or direction == "range":
        return "neutral"
    better = delta < 0 if direction == "lower" else delta > 0
    return "good" if better else "bad"
