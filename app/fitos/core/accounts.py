"""Accounts: who can sign in, their role, and which WhatsApp group (if any) is theirs.

Lives in its own small database, `$DATA_DIR/accounts.sqlite`, next to the per-person databases
(`core/tenancy.py`). It holds no health data: username, display name, bcrypt hash, role, active flag and the
WhatsApp group id. The schema is tiny and created here (`PRAGMA user_version` numbers its versions).

Roles: `admin` manages accounts and can look at anyone's dashboard (read-only); `user` sees only their own.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import bcrypt
from sqlalchemy import Boolean, String, create_engine, event, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from .settings import get_settings
from .tenancy import valid_username

SCHEMA_VERSION = 1
ROLES = ("admin", "user")
MIN_PASSWORD = 10
_DUMMY = bcrypt.hashpw(b"not-a-password", bcrypt.gensalt(rounds=10))


class AccountsBase(DeclarativeBase):
    pass


class Account(AccountsBase):
    __tablename__ = "accounts"
    username: Mapped[str] = mapped_column(String(32), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(10), default="user")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    wa_chat_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.current_timestamp())
    last_login_at: Mapped[datetime | None] = mapped_column()

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


_engine: Engine | None = None
_factory: sessionmaker[Session] | None = None


def default_url() -> str:
    return f"sqlite:///{get_settings().data_dir / 'accounts.sqlite'}"


def _pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def init(url: str | None = None) -> Engine:
    """Open (and create or upgrade) the accounts database. Without `url`, keeps the one already open."""
    global _engine, _factory
    if url is None and _engine is not None:
        return _engine
    url = url or default_url()
    kwargs: dict = {"connect_args": {"check_same_thread": False}}
    if ":memory:" in url or url == "sqlite://":
        from sqlalchemy.pool import StaticPool  # noqa: PLC0415

        kwargs["poolclass"] = StaticPool
    else:
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(url, future=True, **kwargs)
    event.listen(eng, "connect", _pragmas)
    AccountsBase.metadata.create_all(eng)
    with eng.begin() as c:
        if c.execute(text("PRAGMA user_version")).scalar() < SCHEMA_VERSION:
            c.execute(text(f"PRAGMA user_version={SCHEMA_VERSION}"))
    if _engine is not None:
        _engine.dispose()
    _engine, _factory = eng, sessionmaker(bind=eng, expire_on_commit=False, future=True)
    return eng


def _session() -> Session:
    if _factory is None:
        init()
    assert _factory is not None
    return _factory()


def path() -> Path | None:
    if _engine is None or _engine.url.database in (None, "", ":memory:"):
        return None
    return Path(_engine.url.database)


# ── reads ────────────────────────────────────────────────────────────────────────────────────────────────
def get(username: str) -> Account | None:
    with _session() as s:
        return s.get(Account, (username or "").strip().lower())


def all_accounts(active_only: bool = False) -> list[Account]:
    with _session() as s:
        q = select(Account).order_by(Account.role, Account.username)
        if active_only:
            q = q.where(Account.active.is_(True))
        return list(s.scalars(q).all())


def active_usernames() -> list[str]:
    return [a.username for a in all_accounts(active_only=True)]


def by_chat(chat_id: str) -> Account | None:
    if not chat_id:
        return None
    with _session() as s:
        return s.scalar(select(Account).where(Account.wa_chat_id == chat_id, Account.active.is_(True)))


def wa_accounts() -> list[Account]:
    """Active accounts with a WhatsApp group."""
    return [a for a in all_accounts(active_only=True) if a.wa_chat_id]


def first_admin() -> Account | None:
    return next((a for a in all_accounts(active_only=True) if a.is_admin), None)


def primary() -> Account | None:
    """The account that gets server-wide WhatsApp alerts: the first admin with a group."""
    return next((a for a in wa_accounts() if a.is_admin), None)


# ── passwords ────────────────────────────────────────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def check_password(username: str, password: str) -> Account | None:
    """The active account if the password matches. An unknown user still pays for a bcrypt check."""
    acc = get(username)
    h = (acc.password_hash or "").encode() if acc and acc.active else b""
    try:
        ok = bcrypt.checkpw(password.encode(), h or _DUMMY)
    except ValueError:
        ok = False
    return acc if (h and ok) else None


def password_problem(password: str) -> bool:
    """True when a new password is too short (MIN_PASSWORD)."""
    return len(password or "") < MIN_PASSWORD


# ── writes ───────────────────────────────────────────────────────────────────────────────────────────────
def create(username: str, display_name: str, *, password: str | None = None, password_hash: str | None = None,
           role: str = "user", wa_chat_id: str | None = None) -> Account:
    username = (username or "").strip().lower()
    if not valid_username(username):
        raise ValueError("invalid username")
    if role not in ROLES:
        raise ValueError("invalid role")
    with _session() as s, s.begin():
        if s.get(Account, username) is not None:
            raise ValueError("username taken")
        acc = Account(username=username, display_name=(display_name or username).strip()[:80], role=role,
                      active=True, wa_chat_id=wa_chat_id or None,
                      password_hash=password_hash or (hash_password(password) if password else None))
        s.add(acc)
    return acc


def update(username: str, **fields) -> Account:
    allowed = {"display_name", "role", "active", "wa_chat_id", "last_login_at"}
    with _session() as s, s.begin():
        acc = s.get(Account, username)
        if acc is None:
            raise KeyError(username)
        was_active_admin = acc.role == "admin" and acc.active
        for k, v in fields.items():
            if k not in allowed:
                raise ValueError(k)
            if k == "role" and v not in ROLES:
                raise ValueError("invalid role")
            setattr(acc, k, v)
        still = acc.role == "admin" and acc.active
        if was_active_admin and not still and _other_active_admins(s, username) == 0:
            raise ValueError("last admin")  # someone must always be able to manage accounts
    return acc


def delete(username: str) -> None:
    """Remove the account (the person's data is the caller's business: see users.delete_user)."""
    with _session() as s, s.begin():
        acc = s.get(Account, username)
        if acc is None:
            raise KeyError(username)
        if acc.role == "admin" and acc.active and _other_active_admins(s, username) == 0:
            raise ValueError("last admin")
        s.delete(acc)


def set_password(username: str, password: str) -> None:
    with _session() as s, s.begin():
        acc = s.get(Account, username)
        if acc is None:
            raise KeyError(username)
        acc.password_hash = hash_password(password)


def _other_active_admins(s: Session, username: str) -> int:
    return s.scalar(select(func.count()).select_from(Account).where(
        Account.role == "admin", Account.active.is_(True), Account.username != username)) or 0
