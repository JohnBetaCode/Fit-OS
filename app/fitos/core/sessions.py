"""Dashboard sign-in: accounts and bcrypt hashes from `accounts.sqlite` (`core/accounts.py`), a signed
session cookie (90 days with "remember me"), and a small brake on repeated wrong passwords. The cookie only counts on requests that came through the proxy (`PROXY_SECRET`),
so the app port itself still can't be entered directly."""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections import deque

from .settings import get_settings

COOKIE = "fitos_session"
REMEMBER_DAYS = 90
SESSION_HOURS = 12  # without "remember me": a browser-session cookie whose token also expires
MAX_FAILS, FAIL_WINDOW_S, LOCK_S = 5, 15 * 60, 5 * 60
_fails: deque[float] = deque()


def check_password(user: str, password: str) -> bool:
    """Constant-ish time: an unknown or disabled user still pays for a bcrypt check."""
    from . import accounts  # noqa: PLC0415

    return accounts.check_password(user, password) is not None


def locked(now: float | None = None) -> int:
    """Seconds left before another attempt is allowed (0 = go ahead)."""
    now = now or time.time()
    while _fails and now - _fails[0] > FAIL_WINDOW_S:
        _fails.popleft()
    if len(_fails) >= MAX_FAILS:
        return max(0, int(_fails[-1] + LOCK_S - now))
    return 0


def record_failure(now: float | None = None) -> None:
    _fails.append(now or time.time())


def reset_failures() -> None:
    _fails.clear()


def _key() -> bytes:
    s = get_settings()
    material = f"{s.proxy_secret}\0{s.secrets_key}".encode()
    return hashlib.sha256(b"fitos-session\0" + material).digest()


def sign(user: str, remember: bool, now: float | None = None) -> str:
    exp = int((now or time.time()) + (REMEMBER_DAYS * 86400 if remember else SESSION_HOURS * 3600))
    payload = base64.urlsafe_b64encode(f"{user}|{exp}".encode()).decode().rstrip("=")
    sig = hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify(token: str | None, now: float | None = None) -> str | None:
    """The user of a valid, unexpired token whose account is still active (disabling it signs them out)."""
    if not token or "." not in token or not get_settings().proxy_secret:
        return None
    payload, sig = token.rsplit(".", 1)
    good = hmac.new(_key(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        return None
    try:
        user, exp = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode().rsplit("|", 1)
    except (ValueError, UnicodeDecodeError):
        return None
    from . import accounts  # noqa: PLC0415

    acc = accounts.get(user)
    if int(exp) < (now or time.time()) or acc is None or not acc.active:
        return None
    return user
