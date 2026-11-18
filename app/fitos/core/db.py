"""SQLite (WAL) engines and sessions, one database per person (see `core/tenancy.py`).

`session_scope()`, `get_db()`, `engine()` and `session_factory()` work on the CURRENT user's database: the
code that calls them doesn't change, and without a user they raise instead of guessing. One process writes;
the scheduler and the routes share each user's engine.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from . import tenancy

_engines: dict[str, tuple[Engine, sessionmaker[Session]]] = {}
_lock = threading.Lock()


class MissingDatabase(RuntimeError):
    """The account exists but its database was never created (`fitos users add` creates it)."""


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


def make_engine(url: str) -> Engine:
    kwargs: dict = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        path = url.removeprefix("sqlite:///")
        if path and path != ":memory:" and not path.startswith(":"):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        if ":memory:" in url or url == "sqlite://":
            from sqlalchemy.pool import StaticPool

            kwargs["poolclass"] = StaticPool
    eng = create_engine(url, future=True, **kwargs)
    if url.startswith("sqlite"):
        event.listen(eng, "connect", _sqlite_pragmas)
    return eng


def _register(user: str, url: str) -> Engine:
    eng = make_engine(url)
    old = _engines.get(user)
    _engines[user] = (eng, sessionmaker(bind=eng, expire_on_commit=False, future=True))
    if old is not None:
        old[0].dispose()
    return eng


def init_engine(url: str | None = None, user: str | None = None) -> Engine:
    """(Re)open the database of `user` (default: the current one) at `url` (default: its file under
    `$DATA_DIR/users/`). Tests use it to point a user at an in-memory database."""
    u = user or tenancy.require()
    path = tenancy.db_path(u)
    return _register(u, url or f"sqlite:///{path}")


def _entry(user: str) -> tuple[Engine, sessionmaker[Session]]:
    hit = _engines.get(user)
    if hit is not None:
        return hit
    with _lock:
        hit = _engines.get(user)
        if hit is None:
            path = tenancy.db_path(user)
            if not path.is_file():
                raise MissingDatabase(f"no database for {user!r} at {path}")
            _register(user, f"sqlite:///{path}")
            hit = _engines[user]
    return hit


def dispose(user: str | None = None) -> None:
    """Close one user's engine (or all of them)."""
    for u in [user] if user else list(_engines):
        hit = _engines.pop(u, None)
        if hit is not None:
            hit[0].dispose()


def engine() -> Engine:
    return _entry(tenancy.require())[0]


def session_factory() -> sessionmaker[Session]:
    return _entry(tenancy.require())[1]


@contextmanager
def session_scope() -> Iterator[Session]:
    s = session_factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    """Dependencia de FastAPI."""
    with session_scope() as s:
        yield s
