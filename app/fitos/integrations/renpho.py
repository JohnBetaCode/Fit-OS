"""Renpho smart-scale sync: the person's weigh-ins (weight and body composition) from the Renpho cloud.

Renpho has no public API. This speaks the protocol documented by the open-source `renpho-api` client (MIT,
https://github.com/danvaneijck/renpho-api): JSON bodies AES-128-ECB encrypted with a key shipped in the Renpho
app. It can stop working whenever Renpho changes its backend; the sync then records the error and nothing already
saved is touched.

Credentials live in the person's own data directory (`renpho.json`, mode 0600): the e-mail and the password
encrypted with a key derived from SECRETS_KEY. Never in the database, the logs or the context JSON.

One value per day and metric (`body_metrics`, source "renpho"): the first weigh-in of the day, the comparable
one (fasting, same time). Running the sync again rewrites the same rows: it's idempotent.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol

import httpx
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sqlalchemy.orm import Session

from ..body import service as body
from ..core import prefs, tenancy
from ..core.settings import get_settings
from ..core.timeutil import local_tz, utcnow
from ..i18n import _

log = logging.getLogger("fitos.renpho")

API = "https://cloud.renpho.com"
_KEY = b"ed*wijdi$h6fe3ew"  # the app's AES-128 key (public: it's in every copy of the app)
APP_VERSION = "6.6.0"
ENDPOINTS = {
    "login": "renpho-aggregation/user/login",
    "devices": "renpho-aggregation/device/count",
    "composition": "RenphoHealth/scale/queryBodyCompositionMeasureData",
    "legacy": "RenphoHealth/scale/queryAllMeasureDataList",
}
SCALE_TYPES = [f"{i:02X}" for i in range(1, 0x15)]
OK_CODES = {0, "0", 101, "101", 200, "200", 20000, "20000"}
PAGE = 50
MAX_PAGES = 40  # 2000 weigh-ins per scale is years of daily use; past that something is off

# Renpho field → (our metric kind, sanity bounds). Units as the app reports them.
FIELDS: dict[str, tuple[str, tuple[float, float]]] = {
    "weight": ("weight_kg", (30, 250)),
    "bodyfat": ("body_fat_pct", (3, 60)),
    "muscle": ("muscle_pct", (10, 80)),
    "water": ("water_pct", (20, 80)),
    "visfat": ("visceral_fat", (1, 59)),
    "subfat": ("subcutaneous_fat_pct", (1, 60)),
    "protein": ("protein_pct", (5, 40)),
    "bmr": ("bmr_kcal", (500, 5000)),
    "bodyage": ("body_age", (10, 100)),
    "bmi": ("bmi", (10, 70)),
    "fatFreeWeight": ("lean_mass_kg", (20, 200)),
}
KINDS = [k for k, _b in FIELDS.values()]
STATUS_PREF = "renpho_status"  # {"last_sync", "last_error", "days", "latest"}: shown in Integrations, no secrets
SOURCE = "renpho"


class RenphoError(Exception):
    """Renpho said no (bad credentials, changed API) or couldn't be reached."""


# ── wire format ──────────────────────────────────────────────────────────────────────────────────────────
def _encrypt(obj: Any) -> dict:
    raw = b"" if obj is None else json.dumps(obj, separators=(",", ":")).encode()
    padder = padding.PKCS7(128).padder()
    enc = Cipher(algorithms.AES(_KEY), modes.ECB()).encryptor()  # noqa: S305 - Renpho's protocol, not our choice
    data = enc.update(padder.update(raw) + padder.finalize()) + enc.finalize()
    return {"encryptData": base64.b64encode(data).decode()}


def _decrypt(b64: str) -> Any:
    dec = Cipher(algorithms.AES(_KEY), modes.ECB()).decryptor()  # noqa: S305
    unpadder = padding.PKCS7(128).unpadder()
    raw = dec.update(base64.b64decode(b64)) + dec.finalize()
    return json.loads((unpadder.update(raw) + unpadder.finalize()).decode())


def _records(page: Any) -> list[dict]:
    if isinstance(page, list):
        return [r for r in page if isinstance(r, dict)]
    if isinstance(page, dict):
        for key in ("list", "data", "records", "measurements"):
            if isinstance(page.get(key), list):
                return [r for r in page[key] if isinstance(r, dict)]
        if "weight" in page:
            return [page]
    return []


class Source(Protocol):
    def measurements(self) -> list[dict]: ...


class RenphoClient:
    """The few calls the sync needs: log in, find the scale tables, page through the weigh-ins."""

    def __init__(self, email: str, password: str, *, http: httpx.Client | None = None):
        self.email, self.password = email, password
        self.http = http or httpx.Client(base_url=API, timeout=20.0)
        self.token: str | None = None
        self.user_id: Any = None

    def _post(self, endpoint: str, body: dict, *, auth: bool = True) -> Any:
        headers = {"token": self.token or "", "userId": str(self.user_id), "appVersion": APP_VERSION,
                   "platform": "android"} if auth else {}
        try:
            r = self.http.post(f"/{endpoint}", json=body, headers=headers)
            r.raise_for_status()
            res = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise RenphoError(_("No pude hablar con Renpho: {err}", err=type(e).__name__)) from e
        code, msg = res.get("code"), str(res.get("msg") or "")
        if not (msg.lower() == "success" or code in OK_CODES):
            raise RenphoError(_("Renpho respondió: {msg} (código {code})", msg=msg or "?", code=code))
        return _decrypt(res["data"]) if res.get("data") else None

    def login(self) -> None:
        data = self._post(ENDPOINTS["login"], _encrypt({
            "questionnaire": {},
            "login": {"password": self.password, "areaCode": "US", "appRevision": APP_VERSION,
                      "cellphoneType": "Fit-OS", "systemType": "11", "email": self.email, "platform": "android"},
            "bindingList": {"deviceTypes": SCALE_TYPES},
        }), auth=False) or {}
        info = data.get("login") or {}
        self.token, self.user_id = info.get("token"), info.get("id")
        if not self.token:
            raise RenphoError(_("Renpho no aceptó el correo o la clave."))

    def _pages(self, endpoint: str, table: str, uid: Any) -> list[dict]:
        out: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            rows = _records(self._post(endpoint, _encrypt({"pageNum": page, "pageSize": PAGE,
                                                           "userIds": [str(uid)], "tableName": table})))
            out.extend(rows)
            if len(rows) < PAGE:
                break
        return out

    def measurements(self) -> list[dict]:
        if not self.token:
            self.login()
        try:
            devices = self._post(ENDPOINTS["devices"], _encrypt(None)) or {}
        except RenphoError:  # some accounts want an empty object instead of empty bytes
            devices = self._post(ENDPOINTS["devices"], _encrypt({})) or {}
        out: list[dict] = []
        for scale in devices.get("scale") or []:
            table = scale.get("tableName")
            if not table:
                continue
            uids = scale.get("userIds") or []
            uid = self.user_id if not uids or self.user_id in uids else uids[0]
            rows = self._pages(ENDPOINTS["composition"], table, uid)
            if not rows and scale.get("count"):
                rows = self._pages(ENDPOINTS["legacy"], table, uid)
            out.extend(rows)
        return out


class FakeRenpho:
    """The double for tests and `make dev`: whatever weigh-ins it was given."""

    def __init__(self, records: list[dict] | None = None, error: str | None = None):
        self.records, self.error = records or [], error

    def measurements(self) -> list[dict]:
        if self.error:
            raise RenphoError(self.error)
        return list(self.records)


# ── credentials ──────────────────────────────────────────────────────────────────────────────────────────
def _fernet() -> Fernet:
    secret = get_settings().secrets_key
    if not secret:
        raise RenphoError(_("Falta SECRETS_KEY en el servidor: sin ella no guardo claves."))
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"fitos-renpho\0" + secret.encode()).digest()))


def _creds_path():
    return tenancy.user_dir() / "renpho.json"


def save_credentials(email: str, password: str) -> None:
    p = _creds_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    blob = {"email": email.strip(), "secret": _fernet().encrypt(password.encode()).decode()}
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(blob, f)


def credentials() -> tuple[str, str] | None:
    p = _creds_path()
    if not p.is_file():
        return None
    try:
        blob = json.loads(p.read_text())
        return blob["email"], _fernet().decrypt(blob["secret"].encode()).decode()
    except (OSError, ValueError, KeyError, InvalidToken) as e:
        raise RenphoError(_("No pude leer la conexión con Renpho: vuelve a conectarla.")) from e


def linked_email() -> str | None:
    p = _creds_path()
    try:
        return json.loads(p.read_text()).get("email") if p.is_file() else None
    except (OSError, ValueError):
        return None


def forget() -> None:
    _creds_path().unlink(missing_ok=True)


# ── the sync ─────────────────────────────────────────────────────────────────────────────────────────────
def _when(rec: dict) -> datetime | None:
    local = rec.get("localCreatedAt")
    if isinstance(local, str) and local[:10].count("-") == 2:
        try:
            return datetime.fromisoformat(local.replace("T", " ")[:19])
        except ValueError:
            pass
    ts = rec.get("timeStamp") or rec.get("time_stamp")
    if isinstance(ts, int | float) and ts > 0:
        return datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts, local_tz()).replace(tzinfo=None)
    return None


def first_per_day(records: list[dict]) -> dict[date, dict[str, float]]:
    """Day → {kind: value} from the first weigh-in of each day, values outside sane bounds dropped."""
    firsts: dict[date, tuple[datetime, dict]] = {}
    for rec in records:
        when = _when(rec)
        w = rec.get("weight")
        if when is None or not isinstance(w, int | float) or w <= 0:
            continue
        if when.date() not in firsts or when < firsts[when.date()][0]:
            firsts[when.date()] = (when, rec)
    out: dict[date, dict[str, float]] = {}
    for day, (_t, rec) in firsts.items():
        vals = {}
        for field, (kind, (lo, hi)) in FIELDS.items():
            v = rec.get(field)
            if isinstance(v, int | float) and lo <= v <= hi:
                vals[kind] = round(float(v), 2)
        out[day] = vals
    return out


@dataclass
class SyncResult:
    days: int
    latest: date | None


def sync(db: Session, source: Source) -> SyncResult:
    """Pull every weigh-in and write one row per day and metric. Errors are recorded in the status pref."""
    status = dict(prefs.get(db, STATUS_PREF) or {})
    status["last_sync"] = utcnow().isoformat(timespec="seconds")
    try:
        days = first_per_day(source.measurements())
    except RenphoError as e:
        status["last_error"] = str(e)
        prefs.set_(db, STATUS_PREF, status)
        raise
    for day, vals in days.items():
        for kind, v in vals.items():
            body.log_metric(db, kind, v, on=day, source=SOURCE)
    latest = max(days) if days else None
    status.update({"last_error": None, "days": len(days), "latest": latest.isoformat() if latest else None})
    prefs.set_(db, STATUS_PREF, status)
    return SyncResult(len(days), latest)


def client_for_current_user() -> Source | None:
    """The real client for whoever is running; None when they haven't linked Renpho."""
    creds = credentials()
    return RenphoClient(*creds) if creds else None
