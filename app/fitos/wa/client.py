"""Cliente de WAHA (uno compartido por la red `wa-shared`, o el del perfil `waha`) y su doble para desarrollo.

Media en base64: los clips pesan 30–150 KB y así WAHA no necesita alcanzar una URL de Fit-OS.
"""

from __future__ import annotations

import base64
import contextlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from ..core.settings import get_settings

log = logging.getLogger("fitos.wa.client")


class WahaError(Exception):
    pass


def bare_id(wa_msg_id: Any) -> str:
    """Parte del mensaje de un id de WhatsApp: `3EB0…`, `true_<chat>_3EB0…` o
    `true_<chat>_3EB0…_<participant>@lid` → `3EB0…`."""
    if isinstance(wa_msg_id, dict):
        wa_msg_id = wa_msg_id.get("_serialized") or wa_msg_id.get("id") or ""
    s = str(wa_msg_id or "").strip()
    parts = s.split("_")
    if len(parts) >= 3 and parts[0] in ("true", "false"):
        return parts[2]
    return s


def normalize_groups(rows: Any) -> list[dict]:
    """NOWEB responde un dict por id; otros motores, una lista. Devuelve [{id, name}] por nombre."""
    if isinstance(rows, dict):
        items = [{**g, "id": g.get("id") or k} for k, g in rows.items() if isinstance(g, dict)]
    elif isinstance(rows, list):
        items = [g for g in rows if isinstance(g, dict)]
    else:
        items = []
    out = []
    for g in items:
        gid = g.get("id")
        if isinstance(gid, dict):
            gid = gid.get("_serialized") or gid.get("id")
        name = g.get("subject") or (g.get("groupMetadata") or {}).get("subject") or g.get("name") or ""
        if gid:
            out.append({"id": str(gid), "name": str(name)})
    out.sort(key=lambda x: x["name"].lower())
    return out


class WaClient(Protocol):
    async def send_text(self, chat_id: str, text: str) -> str: ...
    async def send_video(self, chat_id: str, path: Path, caption: str) -> str: ...
    async def send_image(self, chat_id: str, path: Path, caption: str) -> str: ...
    async def start_typing(self, chat_id: str) -> None: ...
    async def stop_typing(self, chat_id: str) -> None: ...
    async def session_info(self) -> dict: ...
    async def download(self, url: str) -> tuple[bytes, str]: ...
    async def groups(self) -> list[dict]: ...


class LiveWaha:
    def __init__(self) -> None:
        s = get_settings()
        self.session = s.waha_session
        self._client = httpx.AsyncClient(
            base_url=s.waha_url, headers={"X-Api-Key": s.waha_api_key}, timeout=httpx.Timeout(20.0)
        )

    async def _post(self, path: str, body: dict, timeout: float = 20.0) -> dict:
        try:
            r = await self._client.post(path, json={"session": self.session, **body}, timeout=timeout)
        except httpx.HTTPError as e:
            raise WahaError(f"{type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            raise WahaError(f"HTTP {r.status_code}: {r.text[:200]}")
        try:
            data = r.json()
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _sent_id(data: dict) -> str:
        return bare_id(data.get("id") or (data.get("key") or {}).get("id") or "")

    async def send_text(self, chat_id: str, text: str) -> str:
        return self._sent_id(await self._post("/api/sendText", {"chatId": chat_id, "text": text}))

    async def send_video(self, chat_id: str, path: Path, caption: str) -> str:
        data = base64.b64encode(path.read_bytes()).decode()
        body = {
            "chatId": chat_id,
            "file": {"mimetype": "video/mp4", "filename": path.name, "data": data},
            "caption": caption,
            "asNote": False,
            "convert": False,
        }
        return self._sent_id(await self._post("/api/sendVideo", body, timeout=120.0))

    async def send_image(self, chat_id: str, path: Path, caption: str) -> str:
        data = base64.b64encode(path.read_bytes()).decode()
        body = {
            "chatId": chat_id,
            "file": {"mimetype": "image/jpeg", "filename": path.name, "data": data},
            "caption": caption,
        }
        return self._sent_id(await self._post("/api/sendImage", body, timeout=60.0))

    async def start_typing(self, chat_id: str) -> None:
        with contextlib.suppress(WahaError):
            await self._post("/api/startTyping", {"chatId": chat_id}, timeout=5.0)

    async def stop_typing(self, chat_id: str) -> None:
        with contextlib.suppress(WahaError):
            await self._post("/api/stopTyping", {"chatId": chat_id}, timeout=5.0)

    async def download(self, url: str) -> tuple[bytes, str]:
        """A file WAHA downloaded from a message (`payload.media.url`). WAHA builds that URL with its own
        public base (often `localhost:3000`), so only the path is used, against `WAHA_URL`."""
        u = httpx.URL(url)
        path = u.raw_path.decode()
        if not path.startswith("/api/files/"):
            raise WahaError(f"unexpected media url path: {path[:80]}")
        try:
            r = await self._client.get(path, timeout=60.0)
        except httpx.HTTPError as e:
            raise WahaError(f"{type(e).__name__}: {e}") from e
        if r.status_code >= 400:
            raise WahaError(f"HTTP {r.status_code}")
        return r.content, r.headers.get("content-type", "")

    async def session_info(self) -> dict:
        try:
            r = await self._client.get(f"/api/sessions/{quote(self.session, safe='')}", timeout=10.0)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            return {"status": "UNREACHABLE", "error": str(e)[:200]}

    async def groups(self) -> list[dict]:
        r = await self._client.get(f"/api/{quote(self.session, safe='')}/groups", timeout=60.0)
        r.raise_for_status()
        return normalize_groups(r.json())


@dataclass
class FakeSent:
    chat_id: str
    kind: str
    text: str
    path: str | None = None
    wa_id: str = ""


@dataclass
class FakeWaha:
    """Doble de WAHA: guarda lo "enviado" para `/dev/wa` y los tests."""

    sent: list[FakeSent] = field(default_factory=list)
    files: dict[str, tuple[bytes, str]] = field(default_factory=dict)  # url → (bytes, mimetype) for download()
    _n: int = 0

    def _next_id(self) -> str:
        self._n += 1
        return f"FAKEOUT{self._n:06d}"

    async def send_text(self, chat_id: str, text: str) -> str:
        wid = self._next_id()
        self.sent.append(FakeSent(chat_id, "text", text, wa_id=wid))
        return wid

    async def send_video(self, chat_id: str, path: Path, caption: str) -> str:
        wid = self._next_id()
        self.sent.append(FakeSent(chat_id, "video", caption, str(path), wa_id=wid))
        return wid

    async def send_image(self, chat_id: str, path: Path, caption: str) -> str:
        wid = self._next_id()
        self.sent.append(FakeSent(chat_id, "image", caption, str(path), wa_id=wid))
        return wid

    async def start_typing(self, chat_id: str) -> None:
        return None

    async def stop_typing(self, chat_id: str) -> None:
        return None

    async def session_info(self) -> dict:
        s = get_settings()
        hook = {"url": "http://fitos:8000/webhooks/waha", "events": ["message.any"]}
        return {"name": s.waha_session, "status": "WORKING", "engine": {"engine": "FAKE"}, "config": {"webhooks": [hook]}}

    async def download(self, url: str) -> tuple[bytes, str]:
        if url not in self.files:
            raise WahaError("no such file")
        return self.files[url]

    async def groups(self) -> list[dict]:
        return [{"id": get_settings().wa_gym_chat_id or "120363000000000000@g.us", "name": "💪 Gym (simulado)"}]


_client: WaClient | None = None


def get_client() -> WaClient:
    global _client
    if _client is None:
        _client = FakeWaha() if get_settings().waha_mode == "fake" else LiveWaha()
    return _client


def set_client(c: WaClient | None) -> None:
    global _client
    _client = c
