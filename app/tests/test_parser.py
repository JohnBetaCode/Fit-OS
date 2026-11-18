from __future__ import annotations

import pytest

from fitos.wa.parser import SetEntry, Target, describe, parse_set_log

T40 = Target(8, 12, 40)
T20 = Target(8, 12, 20)
TDB = Target(10, 15, 12, load_kind="per_hand")
TBW = Target(8, 15, None, load_kind="bodyweight")
TDUR = Target(30, 60, None, load_kind="duration")


def E(reps=None, w=None, rir=None, dur=None, bw=False, added=None):
    return SetEntry(reps, w, rir, dur, bw, added)


OK_CASES = [
    # reps × peso
    ("10x40", None, None, [E(10, 40)]),
    ("10 x 40", None, None, [E(10, 40)]),
    ("10×40kg", None, None, [E(10, 40)]),
    ("10*40", None, None, [E(10, 40)]),
    ("10X40", None, None, [E(10, 40)]),
    ("10 x 40 kg", None, None, [E(10, 40)]),
    ("10 reps con 40 kg", None, None, [E(10, 40)]),
    ("10 de 40", None, None, [E(10, 40)]),
    # solo reps: peso objetivo, luego el anterior
    ("10", T40, 38, [E(10, 40)]),
    ("10", None, 38, [E(10, 38)]),
    ("12", TBW, None, [E(12, None, bw=True)]),
    # RIR / RPE / fallo
    ("10x40 rir2", None, None, [E(10, 40, 2)]),
    ("10x40 rir 2", None, None, [E(10, 40, 2)]),
    ("10x40 r2", None, None, [E(10, 40, 2)]),
    ("10x40r1", None, None, [E(10, 40, 1)]),
    ("10x40 @8", None, None, [E(10, 40, 2)]),
    ("10x40 @9.5", None, None, [E(10, 40, 0.5)]),
    ("10x40 rpe 7", None, None, [E(10, 40, 3)]),
    ("10x40 fallo", None, None, [E(10, 40, 0)]),
    ("8x40 al fallo", None, None, [E(8, 40, 0)]),
    ("10 rir 3", T40, None, [E(10, 40, 3)]),
    # decimales y libras
    ("10x22,5", None, None, [E(10, 22.5)]),
    ("10x22.5", None, None, [E(10, 22.5)]),
    ("10x22,5 rir2", None, None, [E(10, 22.5, 2)]),
    ("10x50lb", None, None, [E(10, 22.5)]),
    ("10 x 100 lbs", None, None, [E(10, 45.5)]),
    ("10x45 libras", None, None, [E(10, 20.5)]),
    # varios sets
    ("3x10x40", None, None, [E(10, 40)] * 3),
    ("3x10x40 rir2", None, None, [E(10, 40, 2)] * 3),
    ("10x40, 9x40, 8x40", None, None, [E(10, 40), E(9, 40), E(8, 40)]),
    ("10x40; 9x40", None, None, [E(10, 40), E(9, 40)]),
    ("10x40\n9x40\n8x40 r1", None, None, [E(10, 40), E(9, 40), E(8, 40, 1)]),
    ("10x40,9x40", None, None, [E(10, 40), E(9, 40)]),
    ("10x22,5, 9x22,5", None, None, [E(10, 22.5), E(9, 22.5)]),
    ("12, 10, 8", T40, None, [E(12, 40), E(10, 40), E(8, 40)]),
    ("10x40 y 9x40", None, None, [E(10, 40), E(9, 40)]),
    # peso corporal y lastre
    ("12 bw", None, None, [E(12, None, bw=True)]),
    ("12 pc", None, None, [E(12, None, bw=True)]),
    ("12 +10", None, None, [E(12, 10, bw=True, added=10)]),
    ("8 + 5 kg", None, None, [E(8, 5, bw=True, added=5)]),
    # duraciones
    ("45s", None, None, [E(dur=45)]),
    ("45 seg", None, None, [E(dur=45)]),
    ("1:00", None, None, [E(dur=60)]),
    ("1:30", None, None, [E(dur=90)]),
    ("1 min", None, None, [E(dur=60)]),
    ("45", TDUR, None, [E(dur=45)]),
    ("3x45s", None, None, [E(dur=45)] * 3),
    # desambiguación A×B
    ("40x10", None, None, [E(10, 40)]),
    ("22,5x10", None, None, [E(10, 22.5)]),
    ("40kg x 10", None, None, [E(10, 40)]),
    ("20x10", T20, None, [E(10, 20)]),
    ("12x15", TDB, None, [E(12, 15)]),
    ("20x10", None, None, [E(20, 10)]),
    ("12x10", Target(8, 12, 10), None, [E(12, 10)]),
    # acentos y mayúsculas
    ("10X40 RIR2", None, None, [E(10, 40, 2)]),
]


@pytest.mark.parametrize(("text", "target", "last", "expected"), OK_CASES)
def test_parse_ok(text, target, last, expected):
    r = parse_set_log(text, target, last)
    assert r.ok, r.reason
    assert r.entries == expected


BAD_CASES = [
    "hola",
    "",
    "listo",
    "200x40",
    "10x600",
    "0x40",
    "10x40 rir 12",
    "10x40 @11",
    "40kg",
    "10x40x30x2",
    "10.5x10.5",
    "10x40 me dolió el hombro",
    "15x3x40",
]


@pytest.mark.parametrize("text", BAD_CASES)
def test_parse_rejects(text):
    r = parse_set_log(text, T40, 40)
    assert not r.ok
    assert r.entries == []
    assert r.reason


def test_target_inversion_is_flagged_ambiguous():
    r = parse_set_log("20x10", T20)
    assert r.ok and r.ambiguous
    assert not parse_set_log("10x20", T20).ambiguous


def test_missing_weight_is_ambiguous():
    r = parse_set_log("10")
    assert r.ok and r.ambiguous and r.entries == [E(10, None)]
    assert r.reason == "falta el peso"


def test_last_weight_carries_inside_message():
    r = parse_set_log("10x40, 9, 8")
    assert [e.weight_kg for e in r.entries] == [40, 40, 40]


@pytest.mark.parametrize(
    ("entry", "load_kind", "text"),
    [
        (E(10, 40, 2), "total", "10 reps × 40 kg, RIR 2"),
        (E(10, 22, None), "per_hand", "10 reps × 22 kg c/u"),
        (E(10, 22.5, 1.5), "per_hand", "10 reps × 22,5 kg c/u, RIR 1,5"),
        (E(12, None, bw=True), "bodyweight", "12 reps · peso corporal"),
        (E(12, 10, bw=True, added=10), "bodyweight_plus", "12 reps + 10 kg de lastre"),
        (E(1, 100, 0), "total", "1 rep × 100 kg, RIR 0"),
        (E(dur=45), "duration", "45 s"),
        (E(dur=90), "duration", "1:30 min"),
        (E(8, 30), "assisted", "8 reps × 30 kg de asistencia"),
    ],
)
def test_describe(entry, load_kind, text):
    assert describe(entry, load_kind) == text
