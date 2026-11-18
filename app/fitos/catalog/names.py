"""English exercise names for display: the dataset ships them lowercase ("dumbbell bench press")."""

from __future__ import annotations

import re

_SMALL = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of", "on", "onto", "or", "over", "per",
    "the", "to", "via", "vs", "with", "without", "v.",
}
_UPPER = {"ez": "EZ", "trx": "TRX", "bosu": "BOSU", "ii": "II", "iii": "III", "iv": "IV", "sz": "SZ"}
_ALPHA = re.compile(r"[a-z]")


def _cap(part: str) -> str:
    """Uppercase the first letter, keeping leading punctuation ("(male)" → "(Male)")."""
    m = _ALPHA.search(part)
    return part if m is None else part[: m.start()] + part[m.start()].upper() + part[m.start() + 1 :]


def _word(word: str, first: bool) -> str:
    if word != word.lower():  # already has capitals (an acronym or a hand-edited name): leave it
        return word
    bare = word.strip("()[],;:")
    if bare in _UPPER:
        return word.replace(bare, _UPPER[bare])
    if not first and word in _SMALL:
        return word
    return "-".join(_cap(p) for p in word.split("-"))


def title_en(name: str) -> str:
    """Title case for English names: "barbell close-grip bench press" → "Barbell Close-Grip Bench Press",
    "lever seated hip abduction (on a bench)" → "Lever Seated Hip Abduction (On a Bench)", "ez bar curl" →
    "EZ Bar Curl". Words that already have capitals are kept."""
    words = name.strip().split()
    return " ".join(_word(w, i == 0) for i, w in enumerate(words))
