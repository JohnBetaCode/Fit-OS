"""Body map figures: anatomical male/female outlines (front and back) with one region per muscle, painted with a
color per catalog muscle. The path data is vendored (static/vendor/body, MIT, see CREDITS.md)."""

from __future__ import annotations

import json
from functools import lru_cache

from . import STATIC_DIR

# Regions of the figures → catalog muscles (`primary_muscle` keys). A region with two muscles is painted with
# the one `rank` puts first (the most fatigued, or the least trained this week).
REGIONS: dict[str, list[str]] = {
    "chest": ["chest"], "deltoids": ["delts"], "biceps": ["biceps"], "triceps": ["triceps"],
    "forearm": ["forearms"], "abs": ["abs"], "obliques": ["abs"], "quadriceps": ["quads"],
    "hamstring": ["hamstrings"], "gluteal": ["glutes"], "calves": ["calves"], "tibialis": ["calves"],
    "trapezius": ["traps"], "upper-back": ["lats", "upper_back"], "lower-back": ["lower_back"],
    "adductors": ["hips"], "abductors": ["hips"],
}


@lru_cache
def _data() -> dict:
    return json.loads((STATIC_DIR / "vendor" / "body" / "body-paths.json").read_text(encoding="utf-8"))


def figures(sex: str | None, fills: dict[str, str], tips: dict[str, str], rank: dict[str, float]) -> list[dict]:
    """Front and back figures for `sex` ("female" or anything else = male), each region with its fill/tooltip."""
    data = _data()["female" if sex == "female" else "male"]
    out = []
    for side in ("front", "back"):
        fig = data[side]
        parts = []
        for p in fig["parts"]:
            muscles = REGIONS.get(p["slug"])
            if not muscles:
                parts.append({"paths": p["paths"], "muscles": [], "fill": None, "tip": ""})
                continue
            lead = max(muscles, key=lambda m: rank.get(m, 0.0))
            tip = " · ".join(tips.get(m, m) for m in dict.fromkeys(muscles))
            parts.append({"paths": p["paths"], "muscles": muscles, "fill": fills.get(lead), "tip": tip})
        out.append({"side": side, "viewBox": fig["viewBox"], "parts": parts})
    return out
