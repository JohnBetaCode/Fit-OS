"""Installable on a phone: manifest, icons, Caddy root certificate download, Settings help."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from fitos.core.settings import get_settings
from fitos.main import app


def test_manifest_and_icons_are_served():
    with TestClient(app) as c:
        html = c.get("/").text
        assert 'rel="manifest"' in html and 'apple-touch-icon' in html and 'apple-mobile-web-app-capable' in html
        m = c.get("/static/manifest.webmanifest")
        assert m.status_code == 200 and m.headers["content-type"].startswith("application/manifest+json")
        data = json.loads(m.text)
        assert data["display"] == "standalone" and {i["sizes"] for i in data["icons"]} == {"192x192", "512x512"}
        for n in (180, 192, 512):
            r = c.get(f"/static/icons/icon-{n}.png")
            assert r.status_code == 200 and r.content[:4] == b"\x89PNG"


def test_root_certificate_download():
    f = get_settings().data_dir / "caddy" / "root.crt"
    f.unlink(missing_ok=True)
    with TestClient(app) as c:
        assert c.get("/ajustes/certificado").status_code == 404
        assert "En tu celular" in c.get("/ajustes").text and "fit-os-ca.crt" not in c.get("/ajustes").text
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n")
        r = c.get("/ajustes/certificado")
        assert r.status_code == 200 and "fit-os-ca.crt" in r.headers["content-disposition"]
        assert "fit-os-ca.crt" in c.get("/ajustes").text
    f.unlink()
