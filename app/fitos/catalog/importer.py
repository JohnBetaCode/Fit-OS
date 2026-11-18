"""Descarga del dataset y upsert de `exercises` (docs/design.md §6.2, pasos 1 y 4). Idempotente."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.models import Exercise

DEFAULT_REPO = "https://github.com/hasaneyldrm/exercises-dataset"

# Respaldo de `primary_muscle` desde `target` cuando falta el enriquecimiento (taxonomía de 15 grupos).
TARGET_TO_MUSCLE = {
    "abs": "abs",
    "pectorals": "chest",
    "biceps": "biceps",
    "glutes": "glutes",
    "delts": "delts",
    "triceps": "triceps",
    "upper back": "upper_back",
    "lats": "lats",
    "calves": "calves",
    "quads": "quads",
    "forearms": "forearms",
    "hamstrings": "hamstrings",
    "spine": "lower_back",
    "traps": "traps",
    "adductors": "hips",
    "abductors": "hips",
    "serratus anterior": "chest",
    "levator scapulae": "traps",
}

# Campos que vienen del enriquecimiento y se copian tal cual al modelo.
ENRICHED_FIELDS = (
    "name_es",
    "primary_muscle",
    "secondary_muscles",
    "movement_pattern",
    "mechanic",
    "laterality",
    "load_kind",
    "requires",
    "station",
    "stress_joints",
    "is_staple",
    "difficulty",
)
_LIST_FIELDS = {"secondary_muscles", "requires", "stress_joints"}


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def read_lock(lock_path: Path | None) -> tuple[str, str]:
    """(repo, commit) de `catalog/source.lock`; sin archivo, el repo por defecto en HEAD."""
    if lock_path is None or not lock_path.is_file():
        return DEFAULT_REPO, "HEAD"
    data = yaml.safe_load(lock_path.read_text()) or {}
    return str(data.get("repo") or DEFAULT_REPO), str(data.get("commit") or "HEAD")


def fetch(data_dir: Path, lock_path: Path | None = None) -> dict[str, str]:
    """Clona (`--depth 1`) en `$DATA/catalog/src` o actualiza el clon existente, fijado al commit del lock."""
    repo, commit = read_lock(lock_path)
    src = Path(data_dir) / "catalog" / "src"
    if (src / ".git").is_dir():
        action = "update"
        if commit == "HEAD":  # clon superficial: fetch + reset es más robusto que pull
            _git("-C", str(src), "fetch", "--depth", "1", "origin", "HEAD")
            _git("-C", str(src), "reset", "--hard", "FETCH_HEAD")
    else:
        action = "clone"
        src.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--depth", "1", repo, str(src))
    if commit != "HEAD" and _git("-C", str(src), "rev-parse", "HEAD") != commit:
        _git("-C", str(src), "fetch", "--depth", "1", "origin", commit)
        _git("-C", str(src), "checkout", "--detach", commit)
    return {"action": action, "path": str(src), "commit": _git("-C", str(src), "rev-parse", "HEAD")}


def load_records(src_dir: Path) -> list[dict[str, Any]]:
    return json.loads((Path(src_dir) / "data" / "exercises.json").read_text())


def load_enrichment(path: Path | None) -> dict[str, dict[str, Any]]:
    """`{id: {campos}}`; también acepta una lista de objetos con `id`. Sin archivo → vacío."""
    if path is None or not Path(path).is_file():
        return {}
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return {str(d["id"]): d for d in data}
    return {str(k): v for k, v in data.items()}


def load_equipment_map(path: Path) -> dict[str, str]:
    return {str(k).strip().lower(): str(v) for k, v in (yaml.safe_load(Path(path).read_text()) or {}).items()}


def youtube_search_url(name_en: str) -> str:
    return f"https://www.youtube.com/results?search_query={quote_plus(name_en)}+technique"


def import_catalog(
    db: Session, src_dir: Path, enrichment_path: Path | None, equipment_map_path: Path
) -> dict:
    """Upsert de `exercises`. Conserva `favorite` (y no toca los `custom`); recalcula todo lo demás."""
    records = load_records(src_dir)
    enrichment = load_enrichment(enrichment_path)
    eq_map = load_equipment_map(equipment_map_path)
    existing = {e.id: e for e in db.scalars(select(Exercise))}
    counts = {"total": len(records), "created": 0, "updated": 0, "enabled": 0, "missing_enrichment": 0}
    unmapped: set[str] = set()

    for rec in records:
        ex_id = str(rec["id"])
        ex = existing.get(ex_id)
        if ex is not None and ex.custom:
            continue
        if ex is None:
            ex = Exercise(id=ex_id, favorite=False, custom=False)
            db.add(ex)
            counts["created"] += 1
        else:
            counts["updated"] += 1

        equipment_raw = (rec.get("equipment") or "").strip().lower()
        tag = eq_map.get(equipment_raw)
        if tag is None:
            unmapped.add(equipment_raw)
            tag = "other"
        target = (rec.get("target") or "").strip().lower()
        enr = enrichment.get(ex_id)

        ex.name_en = rec["name"]
        ex.target_raw = target or None
        ex.body_part = rec.get("body_part")
        ex.equipment_raw = equipment_raw or None
        ex.equipment_tag = tag
        steps = rec.get("instruction_steps") or {}
        ex.instructions_es = list(steps.get("es") or [])
        ex.instructions_en = list(steps.get("en") or []) or None
        ex.media_mp4 = f"{ex_id}.mp4"
        ex.media_thumb = f"{ex_id}.jpg"
        ex.media_gif = rec.get("gif_url")
        ex.attribution = rec.get("attribution")
        ex.youtube_url = youtube_search_url(rec["name"])
        for field in ENRICHED_FIELDS:
            default: Any = [] if field in _LIST_FIELDS else None
            value = (enr or {}).get(field, default)
            setattr(ex, field, list(value or []) if field in _LIST_FIELDS else value)
        ex.is_staple = bool(ex.is_staple)
        ex.primary_muscle = ex.primary_muscle or TARGET_TO_MUSCLE.get(target)
        ex.enabled = enr is not None
        counts["enabled" if enr is not None else "missing_enrichment"] += 1

    db.flush()
    counts["unmapped_equipment"] = sorted(unmapped)
    return counts
