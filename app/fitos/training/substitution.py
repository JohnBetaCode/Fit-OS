"""Sustitución determinista (design §7.6): filtros duros y puntaje. También el diccionario de palabras de equipo
(`no tengo mancuernas`) y de articulaciones lesionadas."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Set

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..core.models import Exercise

NO_STATION = {None, "", "none"}

# Palabras del usuario (sin tildes, minúsculas) → tag de equipo o de requisito (§6.3).
EQUIPMENT_WORDS: dict[str, str] = {
    "mancuerna": "dumbbell", "mancuernas": "dumbbell", "pesas": "dumbbell",
    "barra": "barbell", "barras": "barbell", "barra olimpica": "barbell",
    "barra z": "ez_bar", "barra ez": "ez_bar", "barra w": "ez_bar",
    "barra hexagonal": "trap_bar", "trap bar": "trap_bar",
    "polea": "cable", "poleas": "cable", "cable": "cable", "cables": "cable", "crossover": "cable",
    "maquina": "machine", "maquinas": "machine",
    "maquina asistida": "assisted_machine", "asistida": "assisted_machine",
    "banco": "bench_flat", "banco plano": "bench_flat", "banca": "bench_flat",
    "banco inclinado": "bench_incline", "banco declinado": "bench_decline",
    "banco scott": "preacher_bench", "predicador": "preacher_bench",
    "barra de dominadas": "pullup_bar", "barra fija": "pullup_bar", "dominadas": "pullup_bar",
    "paralelas": "dip_bars", "fondos": "dip_bars",
    "smith": "smith", "multipower": "smith", "maquina smith": "smith",
    "rack": "rack", "jaula": "rack", "power rack": "rack",
    "kettlebell": "kettlebell", "kettlebells": "kettlebell", "pesa rusa": "kettlebell", "pesas rusas": "kettlebell",
    "banda": "band", "bandas": "band", "liga": "band", "ligas": "band", "elastico": "band", "elasticos": "band",
    "disco": "plate", "discos": "plate",
    "balon medicinal": "medicine_ball", "pelota": "stability_ball", "fitball": "stability_ball",
    "bola suiza": "stability_ball", "bosu": "bosu",
    "cajon": "box", "caja": "box", "step": "box",
    "trineo": "sled", "cuerda": "rope", "soga": "rope",
    "rodillo": "foam_roller", "foam roller": "foam_roller", "rueda abdominal": "ab_wheel",
    "caminadora": "cardio_machine", "trotadora": "cardio_machine", "bicicleta": "cardio_machine",
    "eliptica": "cardio_machine",
    # English
    "dumbbell": "dumbbell", "dumbbells": "dumbbell", "db": "dumbbell", "dbs": "dumbbell",
    "barbell": "barbell", "barbells": "barbell", "bar": "barbell", "olympic bar": "barbell",
    "ez bar": "ez_bar", "curl bar": "ez_bar", "hex bar": "trap_bar",
    "pulley": "cable", "cable machine": "cable", "cable station": "cable",
    "machine": "machine", "machines": "machine", "assisted machine": "assisted_machine",
    "bench": "bench_flat", "flat bench": "bench_flat", "incline bench": "bench_incline",
    "decline bench": "bench_decline", "preacher bench": "preacher_bench", "preacher": "preacher_bench",
    "pull up bar": "pullup_bar", "pullup bar": "pullup_bar", "chin up bar": "pullup_bar", "chinup bar": "pullup_bar",
    "dip bars": "dip_bars", "dip station": "dip_bars", "dips": "dip_bars",
    "smith machine": "smith", "squat rack": "rack", "cage": "rack", "squat cage": "rack",
    "kb": "kettlebell", "band": "band", "bands": "band", "resistance band": "band", "resistance bands": "band",
    "plate": "plate", "plates": "plate", "weight plates": "plate",
    "medicine ball": "medicine_ball", "med ball": "medicine_ball", "stability ball": "stability_ball",
    "swiss ball": "stability_ball", "exercise ball": "stability_ball",
    "box": "box", "plyo box": "box", "sled": "sled", "rope": "rope", "ab wheel": "ab_wheel",
    "treadmill": "cardio_machine", "bike": "cardio_machine", "elliptical": "cardio_machine", "rower": "cardio_machine",
}

# Área de una lesión (texto libre del perfil) → articulación de `stress_joints`.
JOINT_WORDS: dict[str, str] = {
    "shoulder": "shoulder", "hombro": "shoulder", "hombros": "shoulder",
    "elbow": "elbow", "codo": "elbow", "codos": "elbow",
    "wrist": "wrist", "muneca": "wrist", "munecas": "wrist",
    "lower_back": "lower_back", "lower back": "lower_back", "lumbar": "lower_back", "espalda baja": "lower_back", "zona lumbar": "lower_back",
    "knee": "knee", "rodilla": "knee", "rodillas": "knee",
    "hip": "hip", "cadera": "hip",
    "ankle": "ankle", "tobillo": "ankle", "tobillos": "ankle",
    "neck": "neck", "cuello": "neck", "cervical": "neck",
    "shoulders": "shoulder", "elbows": "elbow", "wrists": "wrist", "low back": "lower_back",
    "knees": "knee", "hips": "hip", "ankles": "ankle",
}


def _fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9_ ]+", " ", t).split())


def _lookup(text: str, table: dict[str, str]) -> str | None:
    t = f" {_fold(text)} "
    for word in sorted(table, key=len, reverse=True):  # la frase más larga gana ("barra de dominadas")
        if f" {word} " in t:
            return table[word]
    return None


def word_to_tag(text: str) -> str | None:
    """'No tengo mancuernas' → 'dumbbell'. Insensible a tildes y mayúsculas; None si no reconoce."""
    return _lookup(text, EQUIPMENT_WORDS)


def active_injured_joints(injuries: Iterable[dict] | None) -> set[str]:
    """Articulaciones de las lesiones activas del perfil (`active` ausente cuenta como activa)."""
    joints: set[str] = set()
    for inj in injuries or []:
        if not isinstance(inj, dict) or inj.get("active", True) is False:
            continue
        if j := _lookup(str(inj.get("area", "")), JOINT_WORDS):
            joints.add(j)
    return joints


def eligible(ex: Exercise, tags: Set[str]) -> bool:
    """Elegible para un perfil: habilitado, `equipment_tag ∈ tags` y `requires ⊆ tags`."""
    return bool(ex.enabled) and ex.equipment_tag in tags and set(ex.requires or []) <= set(tags)


def _jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a or []), set(b or [])
    return len(sa & sb) / len(sa | sb) if sa | sb else 0.0


def score(original: Exercise, cand: Exercise, *, recent_ids: Set[str] = frozenset(),
          history_ids: Set[str] = frozenset()) -> float:
    s = 0.0
    if cand.movement_pattern and cand.movement_pattern == original.movement_pattern:
        s += 50
    if cand.primary_muscle and cand.primary_muscle == original.primary_muscle:
        s += 30
    s += 10 * _jaccard(original.secondary_muscles, cand.secondary_muscles)
    if cand.mechanic and cand.mechanic == original.mechanic:
        s += 10
    if cand.laterality and cand.laterality == original.laterality:
        s += 5
    if cand.is_staple:
        s += 8
    loaded = {"barbell", "dumbbell", "machine", "cable", "smith", "sled", "ez_bar", "trap_bar", "kettlebell"}
    if original.equipment_tag in loaded and cand.equipment_tag in loaded:
        s += 8  # mantener la sobrecarga con carga externa (no cambiar press por fondos)
    if (cand.difficulty or 2) >= 3 and not cand.is_staple:
        s -= 6
    if cand.id in history_ids:
        s += 6
    if cand.id in recent_ids:
        s -= 15
    return round(s, 3)


def rank_alternatives(
    db: Session, original: Exercise, profile_tags: Set[str], *,
    busy_stations: Set[str] = frozenset(), excluded_tags: Set[str] = frozenset(),
    exclude_ids: Set[str] = frozenset(), injured_joints: Set[str] = frozenset(),
    recent_ids: Set[str] = frozenset(), history_ids: Set[str] = frozenset(), limit: int = 5,
) -> list[tuple[Exercise, float]]:
    """Mejores sustitutos de `original`, de mayor a menor puntaje. `exclude_ids` debe traer los ejercicios ya
    en la sesión y los `disliked_exercise_ids`; el original se excluye siempre."""
    tags = set(profile_tags) - set(excluded_tags)
    conds = []
    if original.primary_muscle:
        conds.append(Exercise.primary_muscle == original.primary_muscle)
    if original.movement_pattern:
        conds.append(Exercise.movement_pattern == original.movement_pattern)
    if not conds:
        return []
    rows = db.scalars(select(Exercise).where(Exercise.enabled.is_(True), Exercise.id != original.id, or_(*conds)))
    out: list[tuple[Exercise, float]] = []
    for ex in rows:
        if not eligible(ex, tags) or ex.id in exclude_ids:
            continue
        if ex.station not in NO_STATION and ex.station in busy_stations:
            continue
        if set(ex.stress_joints or []) & set(injured_joints):
            continue
        out.append((ex, score(original, ex, recent_ids=recent_ids, history_ids=history_ids)))
    out.sort(key=lambda p: (-p[1], p[0].display_name, p[0].id))
    return out[:limit]
