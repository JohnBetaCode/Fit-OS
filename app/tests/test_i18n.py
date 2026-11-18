"""Every translatable literal has an English entry, with the same placeholders, and switching works."""

from __future__ import annotations

import re
import string
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from fitos.core import prefs
from fitos.core.db import session_scope
from fitos.i18n import _, en_catalog, lang, pick, set_lang
from fitos.main import app

ROOT = Path(__file__).resolve().parents[1] / "fitos"
PY_CALL = re.compile(r"""(?<![\w.])_\(\s*(?P<q>"|')(?P<s>(?:\\.|(?!(?P=q)).)*)(?P=q)\s*[,)]""")
JINJA_CALL = re.compile(r"""(?<![\w.])_\(\s*(?P<q>"|')(?P<s>(?:\\.|(?!(?P=q)).)*)(?P=q)""")


def _literals() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in ROOT.rglob("*.py"):
        if "i18n" in path.parts:
            continue
        for m in PY_CALL.finditer(path.read_text()):
            found[m["s"].encode().decode("unicode_escape").encode("latin-1").decode("utf-8")] = str(path)
    for path in (ROOT / "web" / "templates").rglob("*.html"):
        for m in JINJA_CALL.finditer(path.read_text()):
            found[m["s"]] = str(path)
    return found


def _fields(s: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(s) if f}


def test_every_literal_has_english():
    cat = en_catalog()
    missing = {s: where for s, where in _literals().items() if s not in cat}
    assert not missing, "missing English for:\n" + "\n".join(f"  {w}: {s!r}" for s, w in sorted(missing.items()))


def test_placeholders_match():
    bad = [k for k, v in en_catalog().items() if _fields(k) != _fields(v)]
    assert not bad, bad


def test_switching_language():
    set_lang("es")
    assert _("Idioma") == "Idioma"
    assert pick("Sentadilla", "Squat") == "Sentadilla"
    set_lang("en")
    assert _("Idioma") == "Language"
    assert pick("Sentadilla", "Squat") == "Squat"
    assert pick(None, "Squat") == "Squat" and pick("Sentadilla", None) == "Sentadilla"


def test_preference_drives_language():
    with session_scope() as db:
        prefs.set_(db, "language", "en")
    assert lang() == "en"
    set_lang(None)
    assert lang() == "en"  # reloaded from the DB
    with session_scope() as db:
        prefs.set_(db, "language", "es")
    assert lang() == "es"


@pytest.mark.parametrize("code", ["es", "en"])
def test_pages_render_in_both_languages(code):
    with session_scope() as db:
        prefs.set_(db, "language", code)
    with TestClient(app) as c:
        for path in ["/", "/entrenos", "/progreso", "/ejercicios", "/examenes", "/coach", "/perfil",
                     "/integraciones", "/ajustes"]:
            assert c.get(path).status_code == 200, path
