"""Account lifecycle: create a person (account + their own database), migrate every database, and adopt the
single-person database from before accounts existed.

The per-person database is a full Fit-OS database (catalog included, so favorites and dislikes stay
personal). Creating one runs the migrations, the default rows and, when the exercise dataset is on disk, the
catalog import.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from .core import accounts, db, tenancy
from .core.settings import get_settings

log = logging.getLogger("fitos.users")
APP_DIR = Path(__file__).resolve().parent.parent


def migrate_db(path: Path) -> None:
    """`alembic upgrade head` on one database file."""
    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = Config(str(APP_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(APP_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{path}".replace("%", "%%"))
    command.upgrade(cfg, "head")


def migrate_all() -> list[str]:
    """Accounts schema plus every account's database (inactive ones too: their data is kept)."""
    accounts.init()
    done = []
    for acc in accounts.all_accounts():
        p = tenancy.db_path(acc.username)
        if p.is_file():
            migrate_db(p)
            done.append(acc.username)
        else:
            log.warning("account %s has no database at %s", acc.username, p)
    return done


def bootstrap(username: str) -> None:
    """Default rows (profile, equipment, biomarkers) in one person's database. Idempotent."""
    from sqlalchemy import func, select  # noqa: PLC0415

    from .body.service import ensure_defaults  # noqa: PLC0415
    from .core.models import Exercise  # noqa: PLC0415
    from .labs.service import ensure_biomarkers  # noqa: PLC0415

    with tenancy.as_user(username), db.session_scope() as s:
        ensure_defaults(s)
        ensure_biomarkers(s)
        empty = not s.scalar(select(func.count()).select_from(Exercise))
    if empty:  # created before the dataset was fetched: catch up now that it may be there
        import_catalog(username)


def import_catalog(username: str) -> dict | None:
    """Exercise bank into one person's database, if the dataset was fetched (`fitos catalog fetch`)."""
    from .catalog.cli import _src_dir, catalog_dir  # noqa: PLC0415
    from .catalog.importer import import_catalog as _import  # noqa: PLC0415

    src, cat = _src_dir(), catalog_dir()
    if not src.is_dir():
        return None
    with tenancy.as_user(username), db.session_scope() as s:
        return _import(s, src, cat / "enrichment.json", cat / "equipment_map.yaml")


def provision(username: str) -> Path:
    """Create (or bring up to date) a person's database: migrations, default rows, catalog."""
    p = tenancy.db_path(username)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.parent.chmod(0o700)
    migrate_db(p)
    db.dispose(username)
    bootstrap(username)  # also imports the catalog when the dataset is on disk
    return p


def create_user(username: str, display_name: str, password: str, role: str = "user") -> accounts.Account:
    username = (username or "").strip().lower()
    if not tenancy.valid_username(username):
        raise ValueError("invalid username")
    if accounts.get(username) is not None:
        raise ValueError("username taken")
    if tenancy.db_path(username).exists():
        raise ValueError("a database for that username already exists")
    try:
        provision(username)
    except Exception:
        db.dispose(username)
        shutil.rmtree(tenancy.user_dir(username), ignore_errors=True)
        raise
    # WA_GYM_CHAT_ID is the group of the install's first admin (the one who set the server up)
    chat = get_settings().wa_gym_chat_id
    first_group = role == "admin" and chat and accounts.by_chat(chat) is None and not accounts.wa_accounts()
    return accounts.create(username, display_name, password=password, role=role,
                           wa_chat_id=chat if first_group else None)


# ── adopting the single-person database ──────────────────────────────────────────────────────────────────
def _legacy_users(path: Path) -> list[tuple[str, str]]:
    """Active `name hash` lines of the old users file ($DATA_DIR/caddy/users.caddy)."""
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 2 and not parts[0].startswith("#"):
            out.append((parts[0], parts[1]))
    return out


def _counts(path: Path) -> dict[str, int]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute("select name from sqlite_master where type='table' order by name")]
        return {t: con.execute(f'select count(*) from "{t}"').fetchone()[0] for t in tables}
    finally:
        con.close()


def adopt_legacy(as_username: str | None = None, display_name: str | None = None) -> str | None:
    """Turn the single-person install into accounts: the database at `$DATA_DIR/db/fitos.sqlite` becomes the
    first admin's, signed in with the password from the old users file.

    Run it with the app stopped. Non-destructive: the database is copied with SQLite's backup API, every
    table's row count is compared, and the old file is renamed (`fitos.sqlite.adopted-<ts>`), never deleted.
    Returns the username, or None when there is nothing to adopt.
    """
    s = get_settings()
    legacy = s.legacy_db_path
    accounts.init()
    if not legacy.is_file():
        return None
    if accounts.all_accounts():
        raise RuntimeError(f"{legacy} still exists but there are accounts already: resolve it by hand")
    old_users = _legacy_users(s.data_dir / "caddy" / "users.caddy")
    if as_username is None:
        if len(old_users) != 1:
            raise RuntimeError(f"the old users file has {len(old_users)} active users: pass --as <username>")
        as_username = old_users[0][0].lower()
    if not tenancy.valid_username(as_username):
        raise ValueError(f"invalid username: {as_username!r} (a-z, 0-9, _ or -; 2-32 characters)")
    hashes = dict(old_users)
    pw_hash = hashes.get(as_username) or (old_users[0][1] if len(old_users) == 1 else None)
    if not pw_hash:
        raise RuntimeError(f"no password for {as_username!r} in the old users file")

    target = tenancy.db_path(as_username)
    if target.exists():
        raise RuntimeError(f"{target} already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    src = sqlite3.connect(str(legacy))
    try:
        src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dst = sqlite3.connect(str(target))
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()
    target.chmod(0o600)
    before, after = _counts(legacy), _counts(target)
    con = sqlite3.connect(str(target))
    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    con.close()
    if before != after or ok != "ok":
        target.unlink()
        raise RuntimeError(f"copy check failed (integrity={ok}); nothing changed")

    old_photos = s.data_dir / "photos"
    if old_photos.is_dir():
        shutil.copytree(old_photos, target.parent / "photos")

    accounts.create(as_username, display_name or as_username.capitalize(), password_hash=pw_hash, role="admin",
                    wa_chat_id=s.wa_gym_chat_id or None)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    for suffix in ("", "-wal", "-shm"):
        f = Path(f"{legacy}{suffix}")
        if f.exists():
            f.rename(f"{legacy}.adopted-{ts}{suffix}")
    if old_photos.is_dir():
        old_photos.rename(s.data_dir / f"photos.adopted-{ts}")
    log.info("adopted %s as %s (%d tables, %d rows)", legacy, as_username, len(after), sum(after.values()))
    return as_username
