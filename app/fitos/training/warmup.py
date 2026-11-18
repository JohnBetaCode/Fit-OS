"""Warm-up ramp before the first heavy lift of a muscle in a session. Pure functions, no database.

A short ramp gets the joints and the movement pattern ready without tiring the working sets: roughly 40–50 %
for 8–10 reps, 60–70 % for 4–5 and, from about 60 kg on a barbell, ~85 % for 1–2 (NSCA, *Essentials of
Strength Training and Conditioning*, 4th ed., ch. 14; Ribeiro et al. 2014, doi:10.1519/JSC.0000000000000451)."""

from __future__ import annotations

from .progression import round_to_available

BAR_KG = 20.0


def needs_warmup(mechanic: str | None, load_kind: str | None, muscle: str | None, warmed: set[str]) -> bool:
    """Compound, externally loaded, and the first exercise of the session for its primary muscle."""
    return mechanic == "compound" and load_kind in ("total", "per_hand") and (muscle or "") not in warmed


def warmup_sets(weight: float | None, load_kind: str | None, equipment_tag: str | None,
                loads: dict) -> list[tuple[int, float | None]]:
    """[(reps, kg)], lightest first. `kg` is per dumbbell for `per_hand`. Without a working weight yet
    (calibration) the ramp has no loads: [(10, None), (5, None)]. Steps that round to the same load, or to the
    working weight itself, are dropped."""
    if not weight:
        return [(10, None), (5, None)]
    barbell = equipment_tag in ("barbell", "ez_bar", "trap_bar", "smith") and load_kind == "total"
    ramp = [(10, 0.45), (5, 0.65)]
    if weight >= (60 if barbell else 40):
        ramp.append((2, 0.85))
    out: list[tuple[int, float | None]] = []
    seen: set[float] = set()
    for reps, pct in ramp:
        kg = round_to_available(weight * pct, load_kind, equipment_tag, loads, direction="down")
        if kg is None:
            continue
        if barbell:
            kg = max(kg, BAR_KG)
        if kg >= weight or kg in seen or kg <= 0:
            continue
        seen.add(kg)
        out.append((reps, kg))
    if barbell and out and out[0][1] and out[0][1] > BAR_KG and weight >= 40:
        out.insert(0, (10, BAR_KG))  # empty bar first
    return out
