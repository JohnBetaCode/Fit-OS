"""Progress photos: stored as files under `$DATA_DIR/users/<name>/photos/<year>/` (mode 600), indexed in `progress_photos`.
They come from WhatsApp (an image sent to the group) or from the Progress tab."""

from __future__ import annotations

import secrets
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import tenancy
from ..core.models import AuditLog, ProgressPhoto
from ..core.timeutil import local_today
from ..i18n import _

MAX_BYTES = 15 * 1024 * 1024
EXT = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic"}


def photos_dir() -> Path:
    """The current person's photos: `$DATA_DIR/users/<name>/photos/`."""
    return tenancy.user_dir() / "photos"


def save(db: Session, data: bytes, mimetype: str, *, taken_on: date | None = None, note: str | None = None,
         source: str = "web", wa_id: str | None = None) -> ProgressPhoto:
    mimetype = (mimetype or "").split(";")[0].strip().lower()
    if mimetype not in EXT:
        raise ValueError(_("Solo fotos (JPG, PNG, WebP o HEIC)"))
    if not data or len(data) > MAX_BYTES:
        raise ValueError(_("La foto pesa más de 15 MB"))
    on = taken_on or local_today()
    rel = Path(str(on.year)) / f"{on.isoformat()}_{secrets.token_hex(4)}.{EXT[mimetype]}"
    path = photos_dir() / rel
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(data)
    path.chmod(0o600)
    ph = ProgressPhoto(taken_on=on, path=str(rel), mimetype=mimetype, size_bytes=len(data),
                       note=(note or "").strip()[:500] or None, source=source, wa_id=wa_id)
    db.add(ph)
    db.flush()
    return ph


def file_of(ph: ProgressPhoto) -> Path | None:
    p = (photos_dir() / ph.path).resolve()
    root = photos_dir().resolve()
    return p if p.is_file() and root in p.parents else None


def delete(db: Session, ph: ProgressPhoto) -> None:
    f = file_of(ph)
    if f:
        f.unlink()
    db.add(AuditLog(actor="user", tool="delete_photo", entity="progress_photo", entity_id=str(ph.id),
                    before={"taken_on": ph.taken_on.isoformat(), "path": ph.path}))
    db.delete(ph)


def by_date(db: Session) -> list[tuple[date, list[ProgressPhoto]]]:
    """Newest day first."""
    groups: dict[date, list[ProgressPhoto]] = {}
    for ph in db.scalars(select(ProgressPhoto).order_by(ProgressPhoto.taken_on.desc(), ProgressPhoto.id)):
        groups.setdefault(ph.taken_on, []).append(ph)
    return list(groups.items())


def count_since(db: Session, since: date) -> int:
    return len(db.scalars(select(ProgressPhoto.id).where(ProgressPhoto.taken_on >= since)).all())
