"""Honest workout titles: what a session says it trained must be what it trained.

A plan's title ("Pierna + abdomen") is a promise; when a session finishes, the groups that got work sets are
known. If the title names a group with no sets (abs skipped, a day cut to cardio), it is rewritten from what was
done ("Pierna + Espalda + Bíceps + Cardio"). A title whose every group was trained is left alone, extra work
included: nothing it says is false.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from ..core.models import WorkoutSession

# catalog primary_muscle → group, and the order groups are named in
MUSCLE_GROUP = {
    "quads": "legs", "hamstrings": "legs", "glutes": "legs", "calves": "legs", "hips": "legs",
    "lats": "back", "upper_back": "back", "lower_back": "back", "traps": "back",
    "chest": "chest", "delts": "shoulders", "biceps": "biceps", "triceps": "triceps", "forearms": "biceps",
    "abs": "abs",
}
ORDER = ["legs", "back", "chest", "shoulders", "biceps", "triceps", "abs", "cardio"]
LABEL_ES = {"legs": "Pierna", "back": "Espalda", "chest": "Pecho", "shoulders": "Hombro", "biceps": "Bíceps",
            "triceps": "Tríceps", "abs": "Abdomen", "cardio": "Cardio"}
# words in a title → group (Spanish and English, accents and plurals folded)
WORDS = {
    "pierna": "legs", "piernas": "legs", "leg": "legs", "legs": "legs", "gluteo": "legs", "gluteos": "legs",
    "cadera": "legs", "cuadriceps": "legs", "isquios": "legs", "pantorrilla": "legs", "pantorrillas": "legs",
    "espalda": "back", "back": "back", "dorsales": "back", "lumbar": "back",
    "pecho": "chest", "chest": "chest", "hombro": "shoulders", "hombros": "shoulders", "shoulder": "shoulders",
    "shoulders": "shoulders", "biceps": "biceps", "triceps": "triceps", "abdomen": "abs", "abs": "abs",
    "core": "abs", "cardio": "cardio",
}
MIN_SETS = 2  # a group counts as trained with at least this many work sets (any cardio counts)


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")


def title_groups(label: str | None) -> set[str]:
    return {WORDS[w] for w in re.findall(r"[a-z]+", _fold(label or "")) if w in WORDS}


def trained_groups(ws: WorkoutSession) -> list[str]:
    counts: Counter[str] = Counter()
    for s in ws.sets:
        if s.is_warmup or s.exercise is None:
            continue
        if s.duration_s and not s.reps:
            counts["cardio"] += MIN_SETS
            continue
        group = MUSCLE_GROUP.get(s.exercise.primary_muscle or "")
        if group:
            counts[group] += 1
    return [g for g in ORDER if counts[g] >= MIN_SETS]


def honest_title(label: str | None, ws: WorkoutSession) -> str | None:
    """The new title when `label` names a group that wasn't trained; None when it can stay."""
    done = trained_groups(ws)
    if not done:
        return None
    claimed = title_groups(label)
    if not claimed or claimed <= set(done):  # names no group we know ("Torso", "Full body") or all trained
        return None
    return " + ".join(LABEL_ES[g] for g in done)
