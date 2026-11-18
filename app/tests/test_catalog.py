"""Catálogo: import con dataset sintético, elegibilidad, búsqueda, media y rutas de la pestaña Ejercicios."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from fitos.catalog import service
from fitos.catalog.importer import import_catalog, load_equipment_map
from fitos.core.models import EquipmentProfile, Exercise, Profile

FIX = Path(__file__).parent / "fixtures" / "catalog"
SRC = FIX / "src"
ENRICH = FIX / "enrichment.json"
EQ_MAP = Path(__file__).resolve().parents[2] / "catalog" / "equipment_map.yaml"

DATASET_EQUIPMENT = [
    "body weight",
    "dumbbell",
    "cable",
    "barbell",
    "leverage machine",
    "band",
    "smith machine",
    "kettlebell",
    "weighted",
    "stability ball",
    "ez barbell",
    "assisted",
    "sled machine",
    "medicine ball",
    "rope",
    "roller",
    "resistance band",
    "bosu ball",
    "olympic barbell",
    "wheel roller",
    "upper body ergometer",
    "skierg machine",
    "hammer",
    "stationary bike",
    "tire",
    "trap bar",
    "elliptical machine",
    "stepmill machine",
]
HOME = ["bodyweight", "dumbbell", "bench_flat", "bench_incline"]


@pytest.fixture
def catalog(db):
    res = import_catalog(db, SRC, ENRICH, EQ_MAP)
    db.flush()
    return res


def test_equipment_map_covers_dataset():
    m = load_equipment_map(EQ_MAP)
    assert set(DATASET_EQUIPMENT) <= set(m)
    assert set(m.values()) <= set(service.EQUIPMENT_ES)
    assert m["weighted"] == "plate" and m["olympic barbell"] == "barbell"


def test_import_fields_and_enrichment(db, catalog):
    assert catalog["total"] == 5 and catalog["created"] == 5 and catalog["enabled"] == 4
    assert catalog["missing_enrichment"] == 1 and catalog["unmapped_equipment"] == []
    ex = db.get(Exercise, "0001")
    assert ex.name_es == "press de banca con mancuernas" and ex.equipment_tag == "dumbbell"
    assert ex.requires == ["bench_flat"] and ex.is_staple and ex.enabled
    assert ex.instructions_es == ["Acuéstate en el banco.", "Empuja las mancuernas."]
    assert (
        ex.media_mp4 == "0001.mp4" and ex.media_thumb == "0001.jpg" and ex.media_gif == "videos/0001-aaa.gif"
    )
    assert ex.youtube_url == "https://www.youtube.com/results?search_query=dumbbell+bench+press+technique"
    assert ex.attribution.startswith("© Gym visual")
    tire = db.get(Exercise, "0005")
    assert not tire.enabled and tire.name_es is None and tire.display_name == "tire flip"
    assert tire.equipment_tag == "other" and tire.primary_muscle == "glutes"  # respaldo desde `target`


def test_import_is_idempotent_and_keeps_favorite(db, catalog):
    db.get(Exercise, "0002").favorite = True
    db.get(Exercise, "0002").name_es = "editado a mano"
    db.flush()
    again = import_catalog(db, SRC, ENRICH, EQ_MAP)
    assert again["created"] == 0 and again["updated"] == 5
    ex = db.get(Exercise, "0002")
    assert ex.favorite and ex.name_es == "dominadas"
    assert db.query(Exercise).count() == 5


def test_import_without_enrichment_disables_all(db, tmp_path):
    res = import_catalog(db, SRC, tmp_path / "no-existe.json", EQ_MAP)
    assert res["enabled"] == 0 and not service.any_enabled(db)


def test_eligibility():
    bench = Exercise(id="x", name_en="x", equipment_tag="dumbbell", requires=["bench_flat"])
    pullup = Exercise(id="y", name_en="y", equipment_tag="bodyweight", requires=["pullup_bar"])
    assert service.eligible(bench, HOME)
    assert not service.eligible(pullup, HOME)
    assert service.eligible(pullup, [*HOME, "pullup_bar"])
    assert not service.eligible(bench, ["dumbbell"])


def ids(rows):
    return [r.id for r in rows]


def test_search_filters(db, catalog):
    assert ids(service.search(db)) == ["0002", "0001", "0003", "0004"]  # básicos primero, por nombre
    assert "0005" in ids(service.search(db, include_disabled=True))
    assert ids(service.search(db, q="PRESS banca")) == ["0001"]
    assert ids(service.search(db, q="dominádas")) == ["0002"]  # tildes indiferentes
    assert ids(service.search(db, q="squat")) == ["0003"]  # también por nombre en inglés
    assert ids(service.search(db, muscle="chest")) == ["0001", "0004"]
    assert ids(service.search(db, equipment_tag="bodyweight")) == ["0002", "0004"]
    assert ids(service.search(db, profile_tags=HOME)) == ["0001", "0004"]  # sin barra de dominadas ni rack
    assert ids(service.search(db, profile_tags=["bodyweight"])) == ["0004"]
    assert ids(service.search(db, pattern="push_horizontal", staples_only=True)) == ["0001"]
    assert ids(service.search(db, limit=2, offset=2)) == ["0003", "0004"]
    service.toggle_favorite(db, "0004")
    assert ids(service.search(db, favorites_only=True)) == ["0004"]


def test_toggles(db, catalog):
    assert db.get(Profile, 1) is None
    assert service.toggle_disliked(db, "0003") is True
    assert db.get(Profile, 1).disliked_exercise_ids == ["0003"]
    assert service.disliked_ids(db) == {"0003"}
    assert service.toggle_disliked(db, "0003") is False
    assert service.disliked_ids(db) == set()
    assert service.toggle_favorite(db, "0001") is True and service.toggle_favorite(db, "0001") is False
    with pytest.raises(KeyError):
        service.toggle_disliked(db, "9999")


# ── Media ───────────────────────────────────────────────────────────────────

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="sin ffmpeg"
)


def _make_gif(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=32x32:d=0.5:r=2",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=32x32:d=0.5:r=2",
            "-filter_complex",
            "[0][1]concat=n=2:v=1,split[a][b];[a]palettegen[p];[b][p]paletteuse",
            "-loop",
            "0",
            str(path),
        ],
        check=True,
    )


def _streams(path: Path) -> list[str]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return out.split()


@needs_ffmpeg
def test_build_media(tmp_path):
    from fitos.catalog.media import build_media, probe_duration

    src = tmp_path / "src"
    recs = [
        {"id": "0001", "name": "a", "image": "images/0001-a.jpg", "gif_url": "videos/0001-a.gif"},
        {"id": "0002", "name": "b", "image": "images/0002-b.jpg", "gif_url": "videos/0002-b.gif"},  # sin GIF
    ]
    (src / "data").mkdir(parents=True)
    (src / "data" / "exercises.json").write_text(json.dumps(recs))
    _make_gif(src / "videos" / "0001-a.gif")
    (src / "images").mkdir()
    (src / "images" / "0001-a.jpg").write_bytes(b"\xff\xd8fake")

    out = tmp_path / "out"
    res = build_media(src, out)
    assert res["built"] == 1 and res["missing"] == 1 and res["thumbs"] == 1 and res["failed"] == []
    mp4 = out / "mp4" / "0001.mp4"
    assert sorted(_streams(mp4)) == ["audio", "video"]
    assert probe_duration(mp4) >= 2.5  # tres vueltas de 1 s
    assert (out / "thumbs" / "0001.jpg").read_bytes() == b"\xff\xd8fake"
    assert not list((out / "mp4").glob("*.part.mp4"))

    again = build_media(src, out, limit=1)
    assert again["built"] == 0 and again["skipped"] == 1 and again["thumbs"] == 0

    silent = build_media(src, tmp_path / "silent", with_audio=False)
    assert _streams(tmp_path / "silent" / "mp4" / "0001.mp4") == ["video"] and silent["built"] == 1


# ── Rutas ───────────────────────────────────────────────────────────────────


@pytest.fixture
def client(db, catalog):
    from fitos.web.routes_exercises import router

    db.add(EquipmentProfile(name="Casa", kind="home", tags=HOME))
    db.commit()
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_routes_index_and_partials(client):
    r = client.get("/ejercicios")
    assert r.status_code == 200
    assert "press de banca con mancuernas" in r.text and "tire flip" not in r.text and "Gym visual" in r.text
    assert "Casa" in r.text  # perfil de equipo en el filtro
    r = client.get("/ejercicios", params={"profile": "1"}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "<html" not in r.text
    assert "flexiones de pecho" in r.text and "dominadas" not in r.text
    r = client.get("/ejercicios", params={"q": "zzz"}, headers={"HX-Request": "true"})
    assert "Ningún ejercicio" in r.text
    r = client.get("/ejercicios", params={"offset": 2, "frag": "items"})
    assert r.status_code == 200 and "<html" not in r.text and "sentadilla" in r.text


def test_routes_detail_and_actions(client, db):
    r = client.get("/ejercicios/0001")
    assert r.status_code == 200
    for text in (
        "/media/mp4/0001.mp4",
        "autoplay loop muted playsinline",
        "Acuéstate en el banco.",
        "Banco plano",
        "youtube.com/results",
        "Tu historial",
        "Gym visual",
    ):
        assert text in r.text
    assert client.get("/ejercicios/9999").status_code == 404

    r = client.post("/ejercicios/0001/favorito")
    assert r.status_code == 200 and "★ Favorito" in r.text
    r = client.post("/ejercicios/0001/descartar")
    assert r.status_code == 200 and "deshacer" in r.text
    assert client.post("/ejercicios/9999/favorito").status_code == 404
    db.expire_all()
    assert db.get(Exercise, "0001").favorite and service.disliked_ids(db) == {"0001"}


def test_history_hook(client):
    from fitos.web import routes_exercises

    routes_exercises.set_history_provider(
        lambda db, ex_id: [{"date": "2026-11-10", "weight_kg": 22.5, "reps": 8, "e1rm_kg": 28.5}]
    )
    try:
        r = client.get("/ejercicios/0001")
        assert "22.5 kg × 8" in r.text
    finally:
        routes_exercises.set_history_provider(None)


def test_source_lock_parses():
    data = yaml.safe_load((EQ_MAP.parent / "source.lock").read_text())
    assert data["repo"].startswith("https://github.com/") and data["commit"]
