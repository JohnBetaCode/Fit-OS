"""WHOOP: recovery, sleep and day strain through WHOOP's official API (v2, OAuth 2.0).

The server owner registers one app at developer-dashboard.whoop.com (redirect
`<PORTAL_BASE_URL>/integraciones/whoop/callback`) and gives Fit-OS its credentials, either in the .env
(WHOOP_CLIENT_ID / WHOOP_CLIENT_SECRET, which win) or as an admin on the Integrations page (encrypted in the
server vault); each person then links their own WHOOP from Integrations.
Fit-OS never sees the WHOOP password: the person signs in at WHOOP and Fit-OS gets tokens, kept encrypted in
their vault (`common.vault_*`). WHOOP rotates the refresh token on every use and invalidates the old one, so
refreshes are serialized per person.

Daily values (`body_metrics`, source "whoop"), dated by the local day you woke up: recovery %, HRV, resting
HR, SpO2, skin temperature, sleep hours / performance / efficiency, respiratory rate; and the day's strain and
energy from the cycle that started that day.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from ..core import prefs, tenancy
from ..core.settings import get_settings
from ..i18n import _
from . import common
from .common import IntegrationError

AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"
API = "https://api.prod.whoop.com/developer"
REVOKE_PATH = "/v2/user/access"
SCOPES = "offline read:recovery read:cycles read:sleep read:workout read:profile read:body_measurement"
CALLBACK_PATH = "/integraciones/whoop/callback"
VAULT = "whoop"
SOURCE = "whoop"
STATUS_PREF = "whoop_status"  # {"last_sync", "last_error", "days", "latest"}
STATE_PREF = "whoop_oauth_state"  # {"state", "at"}: the pending sign-in, checked on the way back
STATE_TTL_S = 900
PAGE = 25  # WHOOP's max
MAX_PAGES = 40
BACKFILL_DAYS = 365  # first link
OVERLAP_DAYS = 3  # later syncs re-read a few days: scores arrive late and get revised
KINDS = ["recovery_pct", "hrv_ms", "resting_hr", "spo2_pct", "skin_temp_c", "sleep_hours", "sleep_performance_pct",
         "sleep_efficiency_pct", "respiratory_rate", "strain", "day_kcal"]
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class WhoopError(IntegrationError):
    """WHOOP said no, or isn't configured on this server."""


APP_VAULT = "whoop_app"  # the server's WHOOP app credentials when an admin enters them on the web


def app_credentials() -> tuple[str, str] | None:
    """(client_id, client_secret): the .env wins; otherwise what an admin saved in Integrations."""
    s = get_settings()
    if s.whoop_client_id and s.whoop_client_secret:
        return s.whoop_client_id, s.whoop_client_secret
    saved = common.server_vault_read(APP_VAULT) or {}
    if saved.get("client_id") and saved.get("client_secret"):
        return saved["client_id"], saved["client_secret"]
    return None


def app_source() -> str | None:
    """Where the app credentials come from: "env", "web" or None (not set)."""
    s = get_settings()
    if s.whoop_client_id and s.whoop_client_secret:
        return "env"
    return "web" if app_credentials() else None


def app_client_id() -> str | None:
    creds = app_credentials()
    return creds[0] if creds else None


def save_app_credentials(client_id: str, client_secret: str) -> None:
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id or not client_secret or len(client_id) > 200 or len(client_secret) > 500:
        raise WhoopError(_("Escribe el Client ID y el Client Secret de la app de WHOOP."))
    common.server_vault_write(APP_VAULT, {"client_id": client_id, "client_secret": client_secret})


def clear_app_credentials() -> None:
    common.server_vault_delete(APP_VAULT)


def configured() -> bool:
    return bool(app_credentials() and get_settings().portal_base_url)


def redirect_uri() -> str:
    return get_settings().portal_base_url.rstrip("/") + CALLBACK_PATH


# ── OAuth ────────────────────────────────────────────────────────────────────────────────────────────────
def authorize_url(db: Session) -> str:
    """Where to send the person to sign in at WHOOP; remembers the state to check on the way back."""
    if not configured():
        raise WhoopError(_("WHOOP no está configurado en el servidor (WHOOP_CLIENT_ID y WHOOP_CLIENT_SECRET)."))
    state = secrets.token_urlsafe(16)
    prefs.set_(db, STATE_PREF, {"state": state, "at": int(time.time())})
    return AUTH_URL + "?" + urlencode({"response_type": "code", "client_id": app_client_id(),
                                       "redirect_uri": redirect_uri(), "scope": SCOPES, "state": state})


def _token_request(form: dict, http: httpx.Client | None = None) -> dict:
    creds = app_credentials()
    if creds is None:
        raise WhoopError(_("WHOOP no está configurado en el servidor (WHOOP_CLIENT_ID y WHOOP_CLIENT_SECRET)."))
    form = {**form, "client_id": creds[0], "client_secret": creds[1]}
    try:
        r = (http or httpx.Client(timeout=20.0)).post(TOKEN_URL, data=form)
    except httpx.HTTPError as e:
        raise WhoopError(_("No pude hablar con WHOOP: {err}", err=type(e).__name__)) from e
    if r.status_code != 200:
        raise WhoopError(_("WHOOP rechazó la autorización ({code}): vuelve a conectarla.", code=r.status_code),
                         r.status_code)
    tok = r.json()
    if not tok.get("access_token"):
        raise WhoopError(_("WHOOP no devolvió un token."))
    return {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token"),
            "expires_at": int(time.time()) + int(tok.get("expires_in") or 3600) - 60, "scope": tok.get("scope")}


def finish_link(db: Session, code: str, state: str, http: httpx.Client | None = None) -> None:
    """The callback: check the state, trade the code for tokens and keep them."""
    pending = prefs.get(db, STATE_PREF) or {}
    prefs.set_(db, STATE_PREF, None)  # one use
    if not code or not state or not secrets.compare_digest(state, str(pending.get("state") or "")) \
            or time.time() - int(pending.get("at") or 0) > STATE_TTL_S:
        raise WhoopError(_("El regreso desde WHOOP no coincide o expiró: intenta conectar de nuevo."))
    tokens = _token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri()}, http)
    if not tokens.get("refresh_token"):
        raise WhoopError(_("WHOOP no dio acceso sin conexión (offline): vuelve a conectarla."))
    common.vault_write(VAULT, tokens)


def linked() -> bool:
    return common.vault_path(VAULT).is_file()


def _lock() -> threading.Lock:
    who = tenancy.require()
    with _locks_guard:
        return _locks.setdefault(who, threading.Lock())


def access_token(http: httpx.Client | None = None, *, force: bool = False) -> str:
    """A valid access token, refreshing (one at a time per person) when it's about to expire."""
    with _lock():
        tok = common.vault_read(VAULT)
        if tok is None:
            raise WhoopError(_("WHOOP no está conectado."))
        if not force and tok.get("expires_at", 0) > time.time():
            return tok["access_token"]
        new = _token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                              "scope": "offline"}, http)
        new["refresh_token"] = new.get("refresh_token") or tok["refresh_token"]
        common.vault_write(VAULT, new)  # the old refresh token is dead now: save before anything else
        return new["access_token"]


def unlink(http: httpx.Client | None = None) -> None:
    """Revoke at WHOOP (best effort) and forget the tokens. Data already saved stays."""
    try:
        token = access_token(http)
        (http or httpx.Client(timeout=10.0)).delete(API + REVOKE_PATH, headers={"Authorization": f"Bearer {token}"})
    except (IntegrationError, httpx.HTTPError):
        pass
    common.vault_delete(VAULT)


# ── reading ──────────────────────────────────────────────────────────────────────────────────────────────
class Source(Protocol):
    def collection(self, path: str, start: datetime) -> list[dict]: ...


class WhoopClient:
    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=20.0)

    def _get(self, path: str, params: dict) -> dict:
        for attempt in (0, 1):
            token = access_token(self.http, force=attempt == 1)
            try:
                r = self.http.get(API + path, params=params, headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError as e:
                raise WhoopError(_("No pude hablar con WHOOP: {err}", err=type(e).__name__)) from e
            if r.status_code == 401 and attempt == 0:
                continue  # expired early: refresh once and retry
            if r.status_code == 429:
                raise WhoopError(_("WHOOP pidió esperar (límite de consultas): lo intento en la próxima hora."), 429)
            if r.status_code != 200:
                raise WhoopError(_("WHOOP respondió {code}.", code=r.status_code), r.status_code)
            return r.json()
        raise WhoopError(_("WHOOP rechazó la autorización ({code}): vuelve a conectarla.", code=401), 401)

    def collection(self, path: str, start: datetime) -> list[dict]:
        out: list[dict] = []
        params: dict[str, Any] = {"limit": PAGE, "start": start.strftime("%Y-%m-%dT%H:%M:%S.000Z")}
        for _page in range(MAX_PAGES):
            data = self._get(path, params)
            out.extend(r for r in data.get("records") or [] if isinstance(r, dict))
            nxt = data.get("next_token")
            if not nxt:
                break
            params["nextToken"] = nxt
        return out


class FakeWhoop:
    """The double for tests: fixed collections by path."""

    def __init__(self, data: dict[str, list[dict]] | None = None, error: str | None = None):
        self.data, self.error = data or {}, error

    def collection(self, path: str, start: datetime) -> list[dict]:
        if self.error:
            raise WhoopError(self.error)
        return list(self.data.get(path, []))


def _local(ts: str | None, offset: str | None) -> datetime | None:
    """A WHOOP UTC timestamp shifted to the wearer's offset ('+hh:mm', '-hh:mm', 'Z')."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None
    if offset and offset != "Z":
        sign = -1 if offset.startswith("-") else 1
        h, m = offset.lstrip("+-").split(":")
        dt += sign * timedelta(hours=int(h), minutes=int(m))
    return dt


def _put(day: dict, kind: str, v: Any, lo: float, hi: float, nd: int = 1) -> None:
    if isinstance(v, int | float) and lo <= v <= hi:
        day[kind] = round(float(v), nd)


def daily(recoveries: list[dict], sleeps: list[dict], cycles: list[dict]) -> dict[date, dict[str, float]]:
    """Day → {kind: value}. Recovery and sleep belong to the day you woke up; strain to the cycle's start day."""
    out: dict[date, dict[str, float]] = {}
    main: dict[date, dict] = {}  # the longest scored non-nap sleep that ended that day
    by_id: dict[str, date] = {}
    for sl in sleeps:
        end = _local(sl.get("end"), sl.get("timezone_offset"))
        if end is None or sl.get("nap") or sl.get("score_state") != "SCORED":
            continue
        st = (sl.get("score") or {}).get("stage_summary") or {}
        asleep = sum(st.get(k) or 0 for k in ("total_light_sleep_time_milli", "total_slow_wave_sleep_time_milli",
                                               "total_rem_sleep_time_milli"))
        prev = main.get(end.date())
        if prev is None or asleep > prev["_asleep"]:
            main[end.date()] = {**sl, "_asleep": asleep}
        by_id[str(sl.get("id"))] = end.date()
    for d, sl in main.items():
        sc, day = sl.get("score") or {}, out.setdefault(d, {})
        _put(day, "sleep_hours", sl["_asleep"] / 3_600_000, 0.5, 16, 2)
        _put(day, "sleep_performance_pct", sc.get("sleep_performance_percentage"), 0, 100)
        _put(day, "sleep_efficiency_pct", sc.get("sleep_efficiency_percentage"), 0, 100)
        _put(day, "respiratory_rate", sc.get("respiratory_rate"), 5, 40)
    for rec in recoveries:
        d = by_id.get(str(rec.get("sleep_id")))
        if d is None or rec.get("score_state") != "SCORED":
            continue
        sc, day = rec.get("score") or {}, out.setdefault(d, {})
        _put(day, "recovery_pct", sc.get("recovery_score"), 0, 100, 0)
        _put(day, "hrv_ms", sc.get("hrv_rmssd_milli"), 5, 300)
        _put(day, "resting_hr", sc.get("resting_heart_rate"), 25, 140, 0)
        _put(day, "spo2_pct", sc.get("spo2_percentage"), 70, 100)
        _put(day, "skin_temp_c", sc.get("skin_temp_celsius"), 25, 42)
    for cy in cycles:
        start = _local(cy.get("start"), cy.get("timezone_offset"))
        if start is None or cy.get("score_state") != "SCORED":
            continue
        sc, day = cy.get("score") or {}, out.setdefault(start.date(), {})
        _put(day, "strain", sc.get("strain"), 0, 21)
        kj = sc.get("kilojoule")
        if isinstance(kj, int | float):
            _put(day, "day_kcal", kj / 4.184, 500, 10000, 0)
    return {d: v for d, v in out.items() if v}


@dataclass
class SyncResult:
    days: int
    latest: date | None
    written: int


def fetch(source: Source, since: date) -> dict[date, dict[str, float]]:
    """The network part (no database open meanwhile)."""
    start = datetime.combine(since - timedelta(days=1), datetime.min.time())
    return daily(source.collection("/v2/recovery", start), source.collection("/v2/activity/sleep", start),
                 source.collection("/v2/cycle", start))


def since_for(db: Session, today: date) -> date:
    st = prefs.get(db, STATUS_PREF) or {}
    latest = st.get("latest")
    try:
        return date.fromisoformat(latest) - timedelta(days=OVERLAP_DAYS) if latest else today - timedelta(BACKFILL_DAYS)
    except ValueError:
        return today - timedelta(days=BACKFILL_DAYS)


def store(db: Session, days: dict[date, dict[str, float]]) -> SyncResult:
    written = common.store_daily(db, days, SOURCE, KINDS)
    prev = (prefs.get(db, STATUS_PREF) or {}).get("latest")
    latest = max(days) if days else None
    if prev and (latest is None or prev > latest.isoformat()):
        latest = date.fromisoformat(prev)
    common.set_status(db, STATUS_PREF, last_error=None, days=len(days),
                      latest=latest.isoformat() if latest else None)
    return SyncResult(len(days), latest, written)


def record_error(db: Session, msg: str | None) -> None:
    common.set_status(db, STATUS_PREF, last_error=msg)


def client_for_current_user() -> Source | None:
    return WhoopClient() if linked() and configured() else None
