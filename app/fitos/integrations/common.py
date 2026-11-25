"""What every integration shares: an encrypted per-person vault and "write only what changed" for daily values.

Vault: `<data dir>/users/<name>/<integration>.json`, mode 0600, the payload encrypted with Fernet under a key
derived from SECRETS_KEY and the integration's name (one leaked key can't open another integration's file).
Never the database: backups, the context JSON and the coach don't need passwords or tokens.

Daily values land in `body_metrics` (kind, day, source): one row per metric and day and integration.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import prefs, tenancy
from ..core.models import BodyMetric
from ..core.settings import get_settings
from ..core.timeutil import utcnow
from ..i18n import _


class IntegrationError(Exception):
    """The outside service said no or couldn't be reached; the message is for the person."""

    def __init__(self, msg: str, code: Any = None):
        super().__init__(msg)
        self.code = code


def _fernet(purpose: str) -> Fernet:
    secret = get_settings().secrets_key
    if not secret:
        raise IntegrationError(_("Falta SECRETS_KEY en el servidor: sin ella no guardo claves."))
    digest = hashlib.sha256(f"fitos-{purpose}\0".encode() + secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def vault_path(name: str) -> Path:
    return tenancy.user_dir() / f"{name}.json"


def vault_write(name: str, data: dict) -> None:
    """Replace the integration's secrets (atomically: a crash never leaves half a token)."""
    p = vault_path(name)
    p.parent.mkdir(parents=True, exist_ok=True)
    blob = {"secret": _fernet(name).encrypt(json.dumps(data).encode()).decode()}
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(blob, f)
    os.replace(tmp, p)


def vault_read(name: str) -> dict | None:
    p = vault_path(name)
    if not p.is_file():
        return None
    try:
        return json.loads(_fernet(name).decrypt(json.loads(p.read_text())["secret"].encode()))
    except (OSError, ValueError, KeyError, InvalidToken) as e:
        raise IntegrationError(_("No pude leer la conexión guardada: vuelve a conectarla.")) from e


def vault_delete(name: str) -> None:
    vault_path(name).unlink(missing_ok=True)


def store_daily(db: Session, days: dict[date, dict[str, float]], source: str, kinds: list[str]) -> int:
    """Write only new or changed values, in the caller's (short) transaction. Returns how many were written."""
    existing = {(r.kind, r.measured_on): r for r in db.scalars(
        select(BodyMetric).where(BodyMetric.source == source, BodyMetric.kind.in_(kinds)))}
    written = 0
    for day, vals in days.items():
        for kind, v in vals.items():
            row = existing.get((kind, day))
            if row is None:
                db.add(BodyMetric(kind=kind, value=v, measured_on=day, source=source))
            elif row.value != v:
                row.value = v
            else:
                continue
            written += 1
    return written


def set_status(db: Session, pref: str, **fields) -> dict:
    """Merge fields into the integration's status pref (shown in Integrations; never secrets)."""
    status = dict(prefs.get(db, pref) or {})
    status.update({"last_sync": utcnow().isoformat(timespec="seconds"), **fields})
    prefs.set_(db, pref, status)
    return status
