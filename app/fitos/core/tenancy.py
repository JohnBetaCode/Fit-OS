"""Which person a piece of work belongs to.

Fit-OS keeps one SQLite database per person (`$DATA_DIR/users/<username>/fitos.sqlite`) plus a small shared
`accounts.sqlite` (see `core/accounts.py`). Every request, WhatsApp message, scheduler run and CLI command
runs "as" one user: a context variable that `core/db.py` reads to pick the database. With no user the code
fails instead of guessing, so one person's data can't end up in another's database.

- Web: `core/auth.py` sets it from the signed-in account (or the account an admin is viewing).
- WhatsApp: `wa/webhook.py` sets it from the group the message came from.
- Scheduler and outbox: they loop over the accounts.
- CLI: `fitos --user <name> …`.
- Tests: `set_fallback()` gives code that runs outside any context (the suite's single user) a default.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from .settings import get_settings

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")

_current: ContextVar[str | None] = ContextVar("fitos_user", default=None)
_fallback: str | None = None


class NoUserContext(RuntimeError):
    """Code that needs a person's database ran without one (a bug: see the module docstring)."""


def valid_username(name: str) -> bool:
    return bool(USERNAME_RE.match(name or ""))


def current() -> str | None:
    return _current.get() or _fallback


def require() -> str:
    u = current()
    if not u:
        raise NoUserContext("no user for this operation (web sign-in, WhatsApp group, scheduler or --user)")
    return u


@contextmanager
def as_user(username: str) -> Iterator[str]:
    """Run the block (and the asyncio tasks it creates) as `username`."""
    if not valid_username(username):
        raise ValueError(f"invalid username: {username!r}")
    token = _current.set(username)
    try:
        yield username
    finally:
        _current.reset(token)


def set_fallback(username: str | None) -> None:
    """Default user for code outside any context. Tests and single-user tools only; production leaves it unset."""
    global _fallback
    _fallback = username


def users_root() -> Path:
    return get_settings().data_dir / "users"


def user_dir(username: str | None = None) -> Path:
    u = username or require()
    if not valid_username(u):
        raise ValueError(f"invalid username: {u!r}")
    return users_root() / u


def db_path(username: str | None = None) -> Path:
    return user_dir(username) / "fitos.sqlite"
