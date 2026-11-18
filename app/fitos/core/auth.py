"""Dashboard access, and whose data each request works on.

Who is signed in (an account in `accounts.sqlite`):
- Caddy in front (`ops/caddy/Caddyfile`) sends `X-Fitos-Proxy` = `PROXY_SECRET`. Only with that secret does the
  session cookie from the sign-in page count (`/entrar`, `core/sessions.py`); without a session, to `/entrar`.
  (The source IP can't be used: docker-proxy replaces it with the gateway's.)
- `Tailscale-User-Login` header (only with TRUST_TAILSCALE_HEADERS): a login in `OWNER_LOGINS` is the first
  admin; any other login falls back to the session cookie.
- In development, `DEV_USER_LOGIN` is the first admin (or the account with that username).
- Hardening: a request from the `wa-shared` network (Docker neighbours that could forge headers) only sees
  `/healthz` and `/webhooks/waha`.

Whose data: the request runs as the signed-in account (`core/tenancy.py`). An admin can pick another account
("view as", cookie `fitos_view`): then every page shows that person's data and nothing can be changed except
the admin pages, the switch itself and signing out.
"""

from __future__ import annotations

import hmac
import ipaddress
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse

from . import accounts, sessions, tenancy
from .settings import get_settings

PUBLIC_PATHS = ("/healthz", "/webhooks/waha")
VIEW_COOKIE = "fitos_view"
ADMIN_PREFIX = "/admin"
LOGIN_PATHS = ("/entrar", "/salir")
STATIC_PREFIX = "/static/"


def _in_peer_net(host: str | None, cidr: str) -> bool:
    if not host or not cidr:
        return False
    try:
        return ipaddress.ip_address(host) in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False


def _from_proxy(request: Request) -> bool:
    secret = get_settings().proxy_secret
    got = request.headers.get("x-fitos-proxy") or ""
    return bool(secret) and hmac.compare_digest(got.encode(), secret.encode())


def current_user(request: Request) -> str | None:
    """Username of the signed-in, active account (None = not signed in)."""
    s = get_settings()
    if s.dev_user_login and s.app_env != "production":
        acc = accounts.get(s.dev_user_login) or accounts.first_admin()
        return acc.username if acc else None
    if s.trust_tailscale_headers:
        login = (request.headers.get("tailscale-user-login") or "").strip().lower()
        if login and login in s.owners:
            acc = accounts.first_admin()
            return acc.username if acc else None
    if _from_proxy(request):
        return sessions.verify(request.cookies.get(sessions.COOKIE))
    return None


def _viewing(request: Request, acc: accounts.Account) -> str:
    """The account whose data this request shows: the admin's pick, otherwise their own."""
    if acc.is_admin:
        other = (request.cookies.get(VIEW_COOKIE) or "").strip().lower()
        if other and other != acc.username and accounts.get(other) is not None:
            return other
    return acc.username


def _writes_allowed_while_viewing(path: str) -> bool:
    return path.startswith(ADMIN_PREFIX) or path in ("/salir", "/ver-como")


class AccessMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        s = get_settings()
        path = request.url.path
        if path in PUBLIC_PATHS:
            return await call_next(request)
        server_host = (request.scope.get("server") or (None, None))[0]
        client_host = request.client.host if request.client else None
        if _in_peer_net(server_host, s.peer_net_cidr) or _in_peer_net(client_host, s.peer_net_cidr):
            return PlainTextResponse("forbidden", status_code=403)
        if path.startswith(STATIC_PREFIX):
            return await call_next(request)
        user = current_user(request)
        acc = accounts.get(user) if user else None
        if acc is None or not acc.active:
            if _from_proxy(request):  # through the portal but not signed in
                if path in LOGIN_PATHS:
                    request.state.user = ""
                    return await call_next(request)
                if request.method == "GET":
                    nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
                    return RedirectResponse(f"/entrar?next={quote(nxt, safe='')}", status_code=303)
                return PlainTextResponse("sign in first", status_code=401)
            return PlainTextResponse("Sin acceso: entra por el portal (Caddy) con tu usuario.", status_code=403)
        if path.startswith(ADMIN_PREFIX) and not acc.is_admin:
            return PlainTextResponse("forbidden", status_code=403)
        viewing = _viewing(request, acc)
        read_only = viewing != acc.username
        if read_only and request.method not in ("GET", "HEAD") and not _writes_allowed_while_viewing(path):
            return PlainTextResponse("read-only: you are viewing another account", status_code=403)
        request.state.user = acc.username
        request.state.account = acc
        request.state.viewing = viewing
        request.state.read_only = read_only
        with tenancy.as_user(viewing):
            return await call_next(request)
