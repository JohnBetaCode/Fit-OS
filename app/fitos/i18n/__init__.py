"""Two languages, Spanish and English, gettext-style: the Spanish text is the key.

- `_("Sesión de hoy")` returns the text in the current language; `_("{n} sets", n=3)` formats after
  translating. Spanish is the source language, so `es` returns the key itself.
- English translations live in `en_*.py` modules next to this file (`EN = {"spanish": "english"}`), one per
  area so they can be edited independently. `tests/test_i18n.py` fails if a `_()` literal in the code or a
  `{{ _("...") }}` in a template has no English entry.
- `pick(es, en)` chooses between two data fields (exercise or biomarker names, descriptions).
- The language is one app-wide preference (single user): `prefs["language"]`, falling back to the
  `DEFAULT_LANGUAGE` setting. It is cached in-process and refreshed when the preference changes.
- WhatsApp commands are understood in both languages whatever the preference (see `wa/session_flow.py`).
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Any

LANGS = ("es", "en")
LANG_NAMES = {"es": "Español", "en": "English"}

_EN: dict[str, str] | None = None
_langs: dict[str | None, str] = {}  # per person (core/tenancy.py); None = outside any user


def _catalog() -> dict[str, str]:
    global _EN
    if _EN is None:
        merged: dict[str, str] = {}
        for mod in pkgutil.iter_modules(__path__):
            if mod.name.startswith("en_"):
                merged.update(importlib.import_module(f"{__name__}.{mod.name}").EN)
        _EN = merged
    return _EN


def _load_lang() -> str:
    from ..core import prefs  # noqa: PLC0415
    from ..core.db import session_scope  # noqa: PLC0415
    from ..core.settings import get_settings  # noqa: PLC0415

    default = get_settings().default_language
    try:
        with session_scope() as db:
            val = prefs.get(db, "language")
    except Exception:  # noqa: BLE001 — no DB yet (migrations, CLI)
        val = None
    return val if val in LANGS else (default if default in LANGS else "en")


def lang() -> str:
    from ..core.tenancy import current  # noqa: PLC0415

    who = current()
    hit = _langs.get(who)
    if hit is None:
        hit = _langs[who] = _load_lang()
    return hit


def set_lang(value: str | None) -> None:
    """Force the cached language (None = reload from prefs on next use)."""
    from ..core.tenancy import current  # noqa: PLC0415

    if value in LANGS:
        _langs[current()] = value
    elif value is None:
        _langs.clear()


def _(text: str, /, **kw: Any) -> str:
    out = text if lang() == "es" else _catalog().get(text, text)
    return out.format(**kw) if kw else out


def pick(es: str | None, en: str | None) -> str:
    """The field for the current language, falling back to the other one."""
    if lang() == "es":
        return es or en or ""
    return en or es or ""


def en_catalog() -> dict[str, str]:
    return dict(_catalog())
