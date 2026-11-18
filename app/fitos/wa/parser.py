"""Parser de registros de set (design §7.4). Regex primero; lo que no entiende devuelve `ok=False` para que el
llamador use el respaldo (Haiku) o pregunte. Puro: sin base de datos ni red.

Spanish and English forms are both accepted: `10x40`, `10 con 40`, `10 reps with 40`, `10 at 40`, `40 for 10`,
`3 sets of 10 at 40`, `al fallo` / `to failure`, `pc` / `bw` / `bodyweight`, `+10`, `lbs` / `pounds`."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

from ..i18n import _, lang

REPS_RANGE = (1, 100)
WEIGHT_RANGE = (0.0, 500.0)
RIR_RANGE = (0.0, 10.0)
DURATION_RANGE = (1, 3600)
LB_TO_KG = 0.4536
WEIGHT_UNITS = {"kg", "lb"}
TIME_UNITS = {"s", "min", ":"}
# Margen para invertir A×B por cercanía al objetivo (design §7.4: "claramente más cerca").
TARGET_MARGIN = 0.5


@dataclass
class SetEntry:
    reps: int | None
    weight_kg: float | None
    rir: float | None = None
    duration_s: int | None = None
    bodyweight: bool = False
    added_kg: float | None = None  # lastre (`12 +10`); también se copia en `weight_kg`


@dataclass
class Target:
    rep_min: int
    rep_max: int
    weight_kg: float | None
    target_rir: int | None = 2
    load_kind: str = "total"


@dataclass
class ParseResult:
    entries: list[SetEntry] = field(default_factory=list)
    ok: bool = False
    ambiguous: bool = False  # la lectura salió de una heurística (objetivo) o falta el peso
    reason: str = ""


# ── Normalización ───────────────────────────────────────────────────────────

_NUM = r"\d+(?:\.\d+)?"
_WORDS = [
    (re.compile(r"[×✕✖*]"), "x"),
    (re.compile(r"\b(?:kilos?|kgs|kilogram(?:o|me)?s?)\b"), "kg"),
    (re.compile(r"\b(?:libras?|lbs|pounds?)\b"), "lb"),
    (re.compile(r"\b(?:segundos?|seconds?|segs?|secs?)\b"), "s"),
    (re.compile(r"\b(?:minutos?|minutes?|mins)\b"), "min"),
]
# Coma decimal: solo en posición de peso (tras x, + o @), seguida de unidad, o como A decimal (`22,5x10`).
_DEC_AFTER = re.compile(r"([x+@]\s*\d{1,3}),(\d{1,2})(?!\d)(?!\s*x)(?=(?<=,5)|(?<=,25)|(?<=,75)|\s|$|kg|lb|r|@|f)")
_DEC_UNIT = re.compile(r"(\d{1,3}),(\d{1,2})(?=\s*(?:kg|lb)\b)")
_DEC_LEAD = re.compile(r"(^|[\s;\n])(\d{1,3}),(\d{1,2})(?=\s*x)")
_SPLIT = re.compile(r"\s*[,;\n]\s*|\s+(?:y|and)\s+")

_FALLO = re.compile(r"\b(?:(?:al\s+)?fallo|(?:to\s+)?failure)\b")
_RPE = re.compile(rf"(?:@|\brpe\s*)\s*({_NUM})")
_RIR = re.compile(rf"(?<![a-z])(?:rir|r)\s*({_NUM})(?![\d.])")
_BW = re.compile(r"\b(?:bw|pc|peso\s+corporal|corporal|sin\s+peso|body\s*weight|no\s+weight)\b")
_ADDED = re.compile(rf"\+\s*({_NUM})\s*(kg|lb)?")
_FILLER = re.compile(r"\b(?:reps?|repeticiones|repetitions?)\b")
_CON = re.compile(r"\s+(?:con|de|with|w/|at|for)\s+")
# `3 sets of 10`, `3 series de 10`: the first number is the set count.
_SETS_OF = re.compile(r"^\s*(\d+)\s*(?:sets?|series)\s*(?:of|de|x)?\s*")
_PART = re.compile(rf"^({_NUM})\s*(kg|lb|s|min)?$")
_CLOCK = re.compile(r"^(\d{1,2}):([0-5]\d)$")


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower().strip()
    for rx, rep in _WORDS:
        t = rx.sub(rep, t)
    t = _DEC_UNIT.sub(r"\1.\2", t)
    t = _DEC_AFTER.sub(r"\1.\2", t)
    t = _DEC_LEAD.sub(r"\1\2.\3", t)
    return t


def _lb(x: float) -> float:
    return math.floor(x * LB_TO_KG * 2 + 0.5) / 2


def _kg(value: float, unit: str | None) -> float:
    return _lb(value) if unit == "lb" else value


# ── Partes ──────────────────────────────────────────────────────────────────


class _Fail(Exception):
    pass


def _part(raw: str) -> tuple[float, str | None]:
    """Un número con su unidad opcional; `1:00` es una duración en segundos con unidad ':'."""
    raw = raw.strip()
    if m := _CLOCK.match(raw):
        return float(int(m[1]) * 60 + int(m[2])), ":"
    if m := _PART.match(raw):
        return float(m[1]), m[2]
    raise _Fail(f"no entendí «{raw}»")


def _seconds(value: float, unit: str | None) -> int:
    return round(value * 60) if unit == "min" else round(value)


def _cost(reps: float, weight: float, target: Target) -> float:
    lo, hi = target.rep_min, target.rep_max
    off = lo - reps if reps < lo else reps - hi if reps > hi else 0
    cost = off / max(hi, 1)
    if target.weight_kg:
        cost += abs(weight - target.weight_kg) / target.weight_kg
    return cost


def _order(a: tuple[float, str | None], b: tuple[float, str | None], target: Target | None):
    """Devuelve (reps, (peso, unidad), invertido_por_objetivo). Convención reps × peso salvo las reglas §7.4."""
    (av, au), (bv, bu) = a, b
    if au in WEIGHT_UNITS and bu not in WEIGHT_UNITS:
        return bv, (av, au), False
    if bu in WEIGHT_UNITS:
        return av, (bv, bu), False
    if not av.is_integer() and bv.is_integer():
        return bv, (av, au), False
    if av > 30 and bv <= 30:
        return bv, (av, au), False
    if target and bv.is_integer() and bv <= 30 and _cost(bv, av, target) + TARGET_MARGIN < _cost(av, bv, target):
        return bv, (av, au), True
    return av, (bv, bu), False


def _reps(v: float) -> int:
    if not v.is_integer():
        raise _Fail("las reps no llevan decimales")
    return int(v)


def _default_weight(target: Target | None, last_weight: float | None) -> float | None:
    if target and target.weight_kg is not None:
        return target.weight_kg
    return last_weight


def _segment(seg: str, target: Target | None, last_weight: float | None) -> tuple[list[SetEntry], bool]:
    rir: float | None = None
    bodyweight = False
    added: float | None = None
    if _FALLO.search(seg):
        rir, seg = 0.0, _FALLO.sub(" ", seg)
    if m := _RPE.search(seg):
        rpe = float(m[1])
        if not 1 <= rpe <= 10:
            raise _Fail("el RPE va de 1 a 10")
        rir, seg = 10 - rpe, _RPE.sub(" ", seg)
    if m := _RIR.search(seg):
        rir, seg = float(m[1]), _RIR.sub(" ", seg)
    if _BW.search(seg):
        bodyweight, seg = True, _BW.sub(" ", seg)
    if m := _ADDED.search(seg):
        added, bodyweight, seg = _kg(float(m[1]), m[2]), True, _ADDED.sub(" ", seg)
    counted = bool(_SETS_OF.match(seg))
    if counted:
        seg = _SETS_OF.sub(r"\1 x ", seg)
    seg = _CON.sub(" x ", _FILLER.sub(" ", seg)).strip(" x")
    if not seg:
        raise _Fail("falta el número de reps")
    parts = [_part(p) for p in re.split(r"\s*x\s*", seg)]

    count, reps, weight, duration, ambiguous = 1, None, None, None, False
    lk = target.load_kind if target else "total"
    if counted and len(parts) == 2:  # `3 sets of 10`: count × reps, weight from the target
        count = _reps(parts[0][0])
        parts = parts[1:]
    if len(parts) == 3:
        count = _reps(parts[0][0])
        if not 1 <= count <= 10:
            raise _Fail("máximo 10 sets en un registro")
        parts = parts[1:]
    if len(parts) == 1:
        (v, u) = parts[0]
        if u in TIME_UNITS or (u is None and lk == "duration"):
            duration = _seconds(v, u)
        elif u in WEIGHT_UNITS:
            raise _Fail("falta el número de reps")
        else:
            reps = _reps(v)
            if added is not None:
                weight = added
            elif not bodyweight and lk != "bodyweight":
                weight = _default_weight(target, last_weight)
                ambiguous = weight is None and lk in ("total", "per_hand")
            bodyweight = bodyweight or lk == "bodyweight"
    elif len(parts) == 2:
        if parts[1][1] in TIME_UNITS:  # 3x45s
            count = _reps(parts[0][0])
            duration = _seconds(*parts[1])
        else:
            r, (w, wu), ambiguous = _order(parts[0], parts[1], target)
            reps, weight = _reps(r), _kg(w, wu)
    else:
        raise _Fail("demasiados números")

    if reps is not None and not REPS_RANGE[0] <= reps <= REPS_RANGE[1]:
        raise _Fail("reps fuera de rango (1–100)")
    for w in (weight, added):
        if w is not None and not WEIGHT_RANGE[0] <= w <= WEIGHT_RANGE[1]:
            raise _Fail("peso fuera de rango (0–500 kg)")
    if rir is not None and not RIR_RANGE[0] <= rir <= RIR_RANGE[1]:
        raise _Fail("RIR fuera de rango (0–10)")
    if duration is not None and not DURATION_RANGE[0] <= duration <= DURATION_RANGE[1]:
        raise _Fail("duración fuera de rango")
    if not 1 <= count <= 10:
        raise _Fail("máximo 10 sets en un registro")
    entry = dict(reps=reps, weight_kg=weight, rir=rir, duration_s=duration, bodyweight=bodyweight, added_kg=added)
    return [SetEntry(**entry) for _ in range(count)], ambiguous


def parse_set_log(text: str, target: Target | None = None, last_weight: float | None = None) -> ParseResult:
    """Interpreta un mensaje con uno o varios sets. `10` solo usa el peso objetivo y, si no hay, el del set
    anterior (`last_weight`). `ambiguous=True` cuando la lectura se invirtió por cercanía al objetivo o falta
    el peso: la confirmación debe mostrar la interpretación (o preguntar)."""
    t = _norm(text or "")
    if not re.search(r"\d", t):
        return ParseResult([], False, reason="sin números")
    entries: list[SetEntry] = []
    ambiguous = False
    try:
        for seg in filter(None, _SPLIT.split(t)):
            got, amb = _segment(seg, target, last_weight)
            entries += got
            ambiguous |= amb
            if entries and entries[-1].weight_kg is not None:
                last_weight = entries[-1].weight_kg
    except _Fail as e:
        return ParseResult([], False, reason=str(e))
    if not entries:
        return ParseResult([], False, reason="vacío")
    return ParseResult(entries, True, ambiguous=ambiguous, reason="falta el peso" if ambiguous and any(
        e.weight_kg is None and not e.bodyweight and e.duration_s is None for e in entries) else "")


# ── Confirmación ────────────────────────────────────────────────────────────


def fmt_num(x: float) -> str:
    """22.5 → '22,5' in Spanish, '22.5' in English; 40.0 → '40'."""
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return s if lang() == "en" else s.replace(".", ",")


def describe(entry: SetEntry, load_kind: str = "total") -> str:
    """Texto de la interpretación: '10 reps × 40 kg, RIR 2'; por mancuerna '× 22 kg c/u' ('× 22 kg each')."""
    parts: list[str] = []
    if entry.duration_s is not None:
        m, s = divmod(entry.duration_s, 60)
        parts.append(f"{entry.duration_s} s" if m == 0 else f"{m}:{s:02d} min")
    if entry.reps is not None:
        parts.append(f"{entry.reps} rep{'s' if entry.reps != 1 else ''}")
    if entry.added_kg:
        parts.append(_("+ {w} kg de lastre", w=fmt_num(entry.added_kg)))
    elif entry.weight_kg is not None and not entry.bodyweight:
        suffix = {"per_hand": _(" c/u"), "assisted": _(" de asistencia")}.get(load_kind, "")
        parts.append(f"× {fmt_num(entry.weight_kg)} kg{suffix}")
    elif entry.bodyweight or load_kind == "bodyweight":
        parts.append(_("· peso corporal"))
    text = " ".join(parts)
    if entry.rir is not None:
        text += f", RIR {fmt_num(entry.rir)}"
    return text
