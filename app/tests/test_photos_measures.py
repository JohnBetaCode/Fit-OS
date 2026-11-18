"""Progress photos (WhatsApp and web) and tape measurements."""

from __future__ import annotations

import io
import zipfile

import pytest
from factories import make_profile
from fastapi.testclient import TestClient
from sqlalchemy import select

from fitos.body import photos
from fitos.body import service as body
from fitos.core.db import session_scope
from fitos.core.models import BodyMetric, ProgressPhoto, WaMessage
from fitos.main import app
from fitos.wa import outbox
from fitos.wa.router import handle_incoming, parse_measurements

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 2000


@pytest.fixture
def world():
    with session_scope() as db:
        make_profile(db, sex="male", onboarded=True)


def test_parse_measurements():
    assert parse_measurements("cintura 84, pecho 102,5") == [("waist_cm", 84.0), ("chest_cm", 102.5)]
    assert parse_measurements("waist 33 and body fat 18 %") == [("waist_cm", 33.0), ("body_fat_pct", 18.0)]
    for text in ("10x40", "8x60 rir2", "peso 80", "hoy", "cintura", "cintura 84 me siento bien"):
        assert parse_measurements(text) == [], text


async def _incoming(**kw) -> int:
    with session_scope() as db:
        m = WaMessage(direction="in", chat_id="g", status="received", **kw)
        db.add(m)
        db.flush()
        return m.id


async def test_measurements_over_whatsapp(world, fake_wa):
    await handle_incoming(await _incoming(body="cintura 84 y brazo 36", kind="text"))
    await outbox.drain_once()
    assert fake_wa.sent[-1].text.startswith("📏 Anotado: cintura 84 cm, brazo 36 cm")
    await handle_incoming(await _incoming(body="cintura 400", kind="text"))
    await outbox.drain_once()
    assert "no parece real" in fake_wa.sent[-1].text
    with session_scope() as db:
        assert {m.kind: m.value for m in db.scalars(select(BodyMetric))} == {"waist_cm": 84, "arm_cm": 36}


async def test_photo_over_whatsapp(world, fake_wa):
    fake_wa.files["http://localhost:3000/api/files/default/ABC.jpeg"] = (JPEG, "image/jpeg")
    media = {"url": "http://localhost:3000/api/files/default/ABC.jpeg", "mimetype": "image/jpeg"}
    await handle_incoming(await _incoming(body="", kind="media", media=media, wa_id="ABC"))
    await handle_incoming(await _incoming(body="progreso frente", kind="media", media=media, wa_id="ABD"))
    await handle_incoming(await _incoming(body="mi almuerzo", kind="media", media=media, wa_id="ABE"))
    await outbox.drain_once()
    texts = [s.text for s in fake_wa.sent]
    assert texts[0].startswith("📸 Foto de progreso guardada (1 este mes)") and "(2 este mes)" in texts[1]
    assert "guardo fotos de progreso" in texts[2]
    with session_scope() as db:
        rows = db.scalars(select(ProgressPhoto).order_by(ProgressPhoto.id)).all()
        assert [(r.source, r.note) for r in rows] == [("wa", None), ("wa", "frente")]
        f = photos.file_of(rows[0])
        assert f.read_bytes() == JPEG and (f.stat().st_mode & 0o777) == 0o600


async def test_photo_without_download(world, fake_wa):
    await handle_incoming(await _incoming(body="", kind="media", media={"url": None}, wa_id="X1"))
    await outbox.drain_once()
    assert "No pude bajar la foto" in fake_wa.sent[-1].text


def test_web_measures_photos_and_export(world):
    with TestClient(app) as c:
        assert c.post("/progreso/medidas", data={"waist_cm": "85,5", "chest_cm": ""}).status_code == 200
        assert c.post("/progreso/medidas", data={"waist_cm": "500"}).status_code == 400
        r = c.post("/progreso/fotos", files=[("files", ("a.jpg", JPEG, "image/jpeg")),
                                             ("files", ("b.jpg", JPEG, "image/jpeg"))])
        assert r.status_code == 200
        assert c.post("/progreso/fotos", files=[("files", ("a.txt", b"hi", "text/plain"))]).status_code == 400
        html = c.get("/progreso").text
        assert "Fotos de progreso" in html and "Primera" not in html and "85.5" in html
        with session_scope() as db:
            ids = [p.id for p in db.scalars(select(ProgressPhoto))]
        img = c.get(f"/fotos/{ids[0]}")
        assert img.status_code == 200 and img.content == JPEG and "private" in img.headers["cache-control"]
        z = zipfile.ZipFile(io.BytesIO(c.get("/ajustes/exportar").content))
        assert sum("/photos/" in n for n in z.namelist()) == 2
        assert c.post(f"/progreso/fotos/{ids[0]}/borrar").status_code == 200
        assert c.get(f"/fotos/{ids[0]}").status_code == 404
    with session_scope() as db:
        assert body.latest_metric(db, "waist_cm").value == 85.5
