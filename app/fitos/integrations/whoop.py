"""WHOOP: recovery, sleep and day strain through WHOOP's official API (v2, OAuth 2.0).

The server owner registers one app at developer-dashboard.whoop.com (redirect
`<PORTAL_BASE_URL>/integraciones/whoop/callback`) and gives Fit-OS its credentials, either in the .env
(WHOOP_CLIENT_ID / WHOOP_CLIENT_SECRET, which win) or as an admin on the Integrations page (encrypted in the
server vault); each person then links their own WHOOP from Integrations.
Fit-OS never sees the WHOOP password: the person signs in at WHOOP and Fit-OS gets tokens, kept encrypted in
their vault (`common.vault_*`). WHOOP rotates the refresh token on every use and invalidates the old one, so
refreshes are serialized per person.

Daily values (`body_metrics`, source "whoop"), dated by the local day you woke up: recovery %, HRV, resting
HR, SpO2, skin temperature; the main sleep (hours, stages, need, debt, consistency, bed and wake times) and naps;
the cycle's strain, energy and heart rate; the day's WHOOP workouts (minutes, strain, energy, HR zones, distance).

Everything WHOOP returns is also kept raw in the person's data dir (`whoop_archive.json.gz`, merged by record
id), so nothing is lost if the membership ends or WHOOP changes its API. The first background sync reads the
whole history (`HISTORY_START`); the link itself only brings the last year so the page answers quickly.
"""

from __future__ import annotations

import gzip
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlencode

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core import prefs, tenancy
from ..core.models import BodyMetric
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
MAX_PAGES = 600  # 15 000 records per collection: decades of history
BACKFILL_DAYS = 365  # what the link itself brings
HISTORY_START = date(2012, 1, 1)  # before any WHOOP strap: "from the beginning"
OVERLAP_DAYS = 3  # later syncs re-read a few days: scores arrive late and get revised
RATE_WAITS = 6  # times to wait out a 429 in one run (100 requests/min)
ARCHIVE = "whoop_archive.json.gz"
COLLECTIONS = {"recovery": "/v2/recovery", "sleep": "/v2/activity/sleep", "cycle": "/v2/cycle",
               "workout": "/v2/activity/workout"}
BODY_PATH = "/v2/user/measurement/body"
KINDS = ["recovery_pct", "hrv_ms", "resting_hr", "spo2_pct", "skin_temp_c",
         "sleep_hours", "sleep_performance_pct", "sleep_efficiency_pct", "respiratory_rate",
         "deep_sleep_h", "rem_sleep_h", "light_sleep_h", "awake_h", "in_bed_h", "sleep_need_h", "sleep_debt_h",
         "sleep_consistency_pct", "sleep_disturbances", "sleep_cycles", "bedtime_h", "wake_h", "nap_min",
         "strain", "day_kcal", "avg_hr", "max_hr",
         "workout_count", "workout_min", "workout_strain", "workout_kcal", "workout_km",
         "hr_zone1_min", "hr_zone2_min", "hr_zone3_min", "hr_zone4_min", "hr_zone5_min",
         "hr_max_profile"]
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

    def one(self, path: str) -> dict | None: ...


class WhoopClient:
    def __init__(self, http: httpx.Client | None = None, sleep=time.sleep):
        self.http = http or httpx.Client(timeout=20.0)
        self.sleep, self.waits = sleep, 0

    def _wait(self, r: httpx.Response) -> None:
        """Wait out the rate limit (a long history read crosses it), a few times per run at most."""
        self.waits += 1
        if self.waits > RATE_WAITS:
            raise WhoopError(_("WHOOP pidió esperar (límite de consultas): lo intento en la próxima hora."), 429)
        try:
            secs = int(r.headers.get("Retry-After") or r.headers.get("X-RateLimit-Reset") or 60)
        except ValueError:
            secs = 60
        self.sleep(min(max(secs, 1), 90))

    def _get(self, path: str, params: dict | None = None) -> dict:
        refreshed = False
        while True:
            token = access_token(self.http, force=refreshed)
            try:
                r = self.http.get(API + path, params=params or {}, headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError as e:
                raise WhoopError(_("No pude hablar con WHOOP: {err}", err=type(e).__name__)) from e
            if r.status_code == 401 and not refreshed:
                refreshed = True  # expired early: refresh once and retry
                continue
            if r.status_code == 429:
                self._wait(r)
                continue
            if r.status_code == 401:
                raise WhoopError(_("WHOOP rechazó la autorización ({code}): vuelve a conectarla.", code=401), 401)
            if r.status_code != 200:
                raise WhoopError(_("WHOOP respondió {code}.", code=r.status_code), r.status_code)
            return r.json()

    def one(self, path: str) -> dict | None:
        try:
            return self._get(path)
        except WhoopError as e:
            if e.code in (403, 404):  # scope not granted (linked before it was asked) or nothing there
                return None
            raise

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
        self.starts = {**getattr(self, "starts", {}), path: start}
        return list(self.data.get(path, []))

    def one(self, path: str) -> dict | None:
        if self.error:
            raise WhoopError(self.error)
        got = self.data.get(path)
        return got if isinstance(got, dict) else None


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


def _h(ms: Any) -> float | None:
    return ms / 3_600_000 if isinstance(ms, int | float) else None


def _clock(t: datetime, wake: bool) -> float:
    """Hour of the day as a number; bedtimes after midnight count past 24 (00:30 → 24.5) so they average well."""
    h = t.hour + t.minute / 60
    return h if wake or h >= 12 else h + 24


def daily(recoveries: list[dict], sleeps: list[dict], cycles: list[dict],
          workouts: list[dict] | None = None) -> dict[date, dict[str, float]]:
    """Day → {kind: value}. Everything belongs to the day you woke up.

    A WHOOP cycle starts when you fall asleep (usually the evening before), so its strain and calories go to the
    wake day of its recovery's sleep; a cycle without one goes to the day of start + 12 h (23:00 → the next day).
    """
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
    naps: dict[date, float] = {}
    for sl in sleeps:
        if sl.get("nap") and sl.get("score_state") == "SCORED":
            end = _local(sl.get("end"), sl.get("timezone_offset"))
            st = (sl.get("score") or {}).get("stage_summary") or {}
            asleep = sum(st.get(k) or 0 for k in ("total_light_sleep_time_milli", "total_slow_wave_sleep_time_milli",
                                                   "total_rem_sleep_time_milli"))
            if end is not None and asleep:
                naps[end.date()] = naps.get(end.date(), 0) + asleep / 60_000
    for d, sl in main.items():
        sc, day = sl.get("score") or {}, out.setdefault(d, {})
        st, need = sc.get("stage_summary") or {}, sc.get("sleep_needed") or {}
        _put(day, "sleep_hours", sl["_asleep"] / 3_600_000, 0.5, 16, 2)
        _put(day, "sleep_performance_pct", sc.get("sleep_performance_percentage"), 0, 100)
        _put(day, "sleep_efficiency_pct", sc.get("sleep_efficiency_percentage"), 0, 100)
        _put(day, "sleep_consistency_pct", sc.get("sleep_consistency_percentage"), 0, 100)
        _put(day, "respiratory_rate", sc.get("respiratory_rate"), 5, 40)
        _put(day, "deep_sleep_h", _h(st.get("total_slow_wave_sleep_time_milli")), 0, 16, 2)
        _put(day, "rem_sleep_h", _h(st.get("total_rem_sleep_time_milli")), 0, 16, 2)
        _put(day, "light_sleep_h", _h(st.get("total_light_sleep_time_milli")), 0, 16, 2)
        _put(day, "awake_h", _h(st.get("total_awake_time_milli")), 0, 16, 2)
        _put(day, "in_bed_h", _h(st.get("total_in_bed_time_milli")), 0, 24, 2)
        _put(day, "sleep_cycles", st.get("sleep_cycle_count"), 0, 30, 0)
        _put(day, "sleep_disturbances", st.get("disturbance_count"), 0, 500, 0)
        parts = [need.get(k) for k in ("baseline_milli", "need_from_sleep_debt_milli", "need_from_recent_strain_milli")]
        if all(isinstance(x, int | float) for x in parts):
            _put(day, "sleep_need_h", _h(sum(parts) - (need.get("need_from_recent_nap_milli") or 0)), 0, 24, 2)
        _put(day, "sleep_debt_h", _h(need.get("need_from_sleep_debt_milli")), 0, 24, 2)
        start, end = _local(sl.get("start"), sl.get("timezone_offset")), _local(sl.get("end"), sl.get("timezone_offset"))
        if start is not None:
            _put(day, "bedtime_h", _clock(start, wake=False), 12, 36, 2)
        if end is not None:
            _put(day, "wake_h", _clock(end, wake=True), 0, 24, 2)
    for d, minutes in naps.items():
        _put(out.setdefault(d, {}), "nap_min", minutes, 1, 600, 0)
    cycle_day: dict[str, date] = {}
    for rec in recoveries:
        d = by_id.get(str(rec.get("sleep_id")))
        if d is not None and rec.get("cycle_id") is not None:
            cycle_day[str(rec["cycle_id"])] = d
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
        d = cycle_day.get(str(cy.get("id"))) or (start + timedelta(hours=12)).date()
        sc, day = cy.get("score") or {}, out.setdefault(d, {})
        _put(day, "strain", sc.get("strain"), 0, 21)
        kj = sc.get("kilojoule")
        if isinstance(kj, int | float):
            _put(day, "day_kcal", kj / 4.184, 500, 10000, 0)
        _put(day, "avg_hr", sc.get("average_heart_rate"), 25, 220, 0)
        _put(day, "max_hr", sc.get("max_heart_rate"), 25, 250, 0)
    sums: dict[date, dict[str, float]] = {}
    for w in workouts or []:
        start, end = _local(w.get("start"), w.get("timezone_offset")), _local(w.get("end"), w.get("timezone_offset"))
        if start is None or end is None or w.get("score_state") != "SCORED":
            continue
        sc, acc = w.get("score") or {}, sums.setdefault(start.date(), {})
        acc["workout_count"] = acc.get("workout_count", 0) + 1
        acc["workout_min"] = acc.get("workout_min", 0) + (end - start).total_seconds() / 60
        if isinstance(sc.get("strain"), int | float):
            acc["workout_strain"] = max(acc.get("workout_strain", 0), sc["strain"])
        if isinstance(sc.get("kilojoule"), int | float):
            acc["workout_kcal"] = acc.get("workout_kcal", 0) + sc["kilojoule"] / 4.184
        if isinstance(sc.get("distance_meter"), int | float):
            acc["workout_km"] = acc.get("workout_km", 0) + sc["distance_meter"] / 1000
        zones = sc.get("zone_durations") or {}
        for n, key in enumerate(("zone_one_milli", "zone_two_milli", "zone_three_milli", "zone_four_milli",
                                 "zone_five_milli"), 1):
            if isinstance(zones.get(key), int | float):
                acc[f"hr_zone{n}_min"] = acc.get(f"hr_zone{n}_min", 0) + zones[key] / 60_000
    for d, acc in sums.items():
        day = out.setdefault(d, {})
        for kind, v in acc.items():
            _put(day, kind, v, 0, 100_000, 0 if kind in ("workout_count", "workout_kcal") else 1)
    return {d: v for d, v in out.items() if v}


@dataclass
class SyncResult:
    days: int
    latest: date | None
    written: int


def _record_key(name: str, r: dict) -> str:
    return str(r.get("cycle_id") if name == "recovery" else r.get("id"))


def archive(raw: dict[str, list[dict]], body: dict | None = None) -> int:
    """Merge every raw record into the person's archive (by id; a newer copy wins). Returns the total kept."""
    p = tenancy.user_dir() / ARCHIVE
    try:
        data = json.loads(gzip.decompress(p.read_bytes())) if p.is_file() else {}
    except (OSError, ValueError):
        data = {}
    for name, records in raw.items():
        bucket = data.setdefault(name, {})
        for r in records:
            bucket[_record_key(name, r)] = r
    if body:
        data["body_measurement"] = body
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(gzip.compress(json.dumps(data, separators=(",", ":")).encode()))
    os.replace(tmp, p)
    return sum(len(v) for k, v in data.items() if isinstance(v, dict) and k in COLLECTIONS)


def fetch(source: Source, since: date, today: date | None = None) -> dict[date, dict[str, float]]:
    """The network part (no database open meanwhile): read, keep the raw copy, turn into days."""
    start = datetime.combine(since - timedelta(days=1), datetime.min.time())
    raw = {name: source.collection(path, start) for name, path in COLLECTIONS.items()}
    body = source.one(BODY_PATH)
    archive(raw, body)
    days = daily(raw["recovery"], raw["sleep"], raw["cycle"], raw["workout"])
    if body and today is not None:
        _put(days.setdefault(today, {}), "hr_max_profile", body.get("max_heart_rate"), 100, 250, 0)
    return days


def since_for(db: Session, today: date, *, history: bool = False) -> date:
    """Where to start reading: the whole history once (background job), else the last days (or a year at link)."""
    st = prefs.get(db, STATUS_PREF) or {}
    if history and not st.get("history_done"):
        return HISTORY_START
    latest = st.get("latest")
    try:
        return date.fromisoformat(latest) - timedelta(days=OVERLAP_DAYS) if latest else today - timedelta(BACKFILL_DAYS)
    except ValueError:
        return today - timedelta(days=BACKFILL_DAYS)


def store(db: Session, days: dict[date, dict[str, float]], since: date | None = None) -> SyncResult:
    written = common.store_daily(db, days, SOURCE, KINDS)
    st = prefs.get(db, STATUS_PREF) or {}
    prev = st.get("latest")
    latest = max(days) if days else None
    if prev and (latest is None or prev > latest.isoformat()):
        latest = date.fromisoformat(prev)
    first = min(days) if days else None
    if st.get("first") and (first is None or st["first"] < first.isoformat()):
        first = date.fromisoformat(st["first"])
    total = db.scalar(select(func.count(func.distinct(BodyMetric.measured_on))).where(BodyMetric.source == SOURCE))
    extra = {"history_done": True} if since == HISTORY_START else {}
    common.set_status(db, STATUS_PREF, last_error=None, days=total or 0, new=written,
                      latest=latest.isoformat() if latest else None, first=first.isoformat() if first else None,
                      **extra)
    return SyncResult(len(days), latest, written)


def record_error(db: Session, msg: str | None) -> None:
    common.set_status(db, STATUS_PREF, last_error=msg)


def client_for_current_user() -> Source | None:
    return WhoopClient() if linked() and configured() else None
