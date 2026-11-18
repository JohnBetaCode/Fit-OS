"""Despacho de los mensajes del grupo. Las rutas solo deciden a quién le toca; la lógica vive en
`session_flow` (entrenos) y `coach` (texto libre)."""

from __future__ import annotations

import logging
import re
import unicodedata

from ..core.db import session_scope
from ..core.models import WaMessage
from ..core.settings import get_settings
from ..i18n import _
from .outbox import enqueue_text

log = logging.getLogger("fitos.wa.router")


def norm(text: str) -> str:
    """Minúsculas, sin tildes, espacios colapsados."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return " ".join(t.lower().split())


HELP_WORDS = {"ayuda", "help", "?", "comandos", "commands", "menu"}


def help_text() -> str:
    """Command list in the current language (commands work in both languages anyway)."""
    return "\n".join([
        "*Fit-OS* 💪",
        _("• *hoy* — el plan de hoy · *2* otro lugar · *3* otro enfoque · *4* descanso"),
        _("• *1* — empezar · durante la sesión: *10x40* (reps x kg), *ocupada*, *otra*, *salta*, *video*, *fin*"),
        _("• en la sesión también: *listo* (siguiente ejercicio), *como* (técnica), *+1* (un set más)"),
        _("• *borra* (último set), *corrige 8x40*, *no tengo barra* (cambio por equipo)"),
        _("• *estado* — dónde va la sesión"),
        _("• *peso 80.5* — registrar peso corporal · *cintura 84, pecho 102* — medidas"),
        _("• una foto sin texto (o con *progreso*) — foto de progreso"),
        _("• *enfermo*, *de viaje*, *lesionado* — marca el día (pausa el plan) · *ya estoy bien*"),
        _("• *debo terminar* — cierra la sesión aunque falten ejercicios"),
        _("• cualquier otra cosa — hablas con el coach"),
    ])


RE_MEASURE = re.compile(r"(body ?fat|[a-z]+)\s*:?\s*(\d{1,3}(?:[.,]\d+)?)\s*(?:cm|%)?")
PHOTO_WORDS = {"progreso", "foto", "fotos", "foto progreso", "progress", "photo", "progress photo", "check in",
               "checkin"}


SICK = {"enfermo", "enferma", "estoy enfermo", "estoy enferma", "hoy enfermo", "hoy enferma", "me siento enfermo",
        "me siento enferma", "me enferme", "gripa", "tengo gripa", "sick", "im sick", "i'm sick", "i am sick",
        "sick today", "feeling sick", "ill", "i'm ill", "im ill", "got sick", "i got sick"}
TRAVEL = {"de viaje", "viajando", "estoy de viaje", "hoy viajo", "travel", "traveling", "travelling",
          "on a trip", "i'm traveling", "im traveling"}
INJURED = {"lesionado", "lesionada", "estoy lesionado", "estoy lesionada", "injured", "i'm injured", "im injured"}
BETTER = {"ya estoy bien", "estoy mejor", "ya me siento bien", "recuperado", "recuperada", "me recupere",
          "ya volvi", "better", "i'm better", "im better", "feeling better", "recovered", "i'm back", "im back"}


def _mark_day(db, kind: str) -> str:
    from ..body import days
    from ..core.timeutil import local_today
    from ..training import service as tr

    today = local_today()
    days.mark(db, today, kind)
    if kind == "sick":
        n = days.streak(db, "sick", today)
        txt = _("🤒 Anotado: hoy enfermo.") + (" " + _("Van {n} días seguidos.", n=n) if n > 1 else "") + " " + _(
            "El plan de hoy queda en pausa; descansa e hidrátate. Cuando estés mejor escribe *ya estoy bien*.")
    elif kind == "travel":
        txt = _("✈️ Anotado: hoy de viaje. El plan de hoy queda en pausa; la rotación sigue donde iba.")
    else:
        txt = _("🩹 Anotado: hoy lesionado. Marca la zona en Perfil → lesiones para que el plan la evite.")
    if tr.active_session(db):
        txt += "\n" + _("Tienes una sesión abierta: escribe *debo terminar* para cerrarla.")
    return txt


def _better(db) -> str:
    from ..body import days

    if days.recently(db, "sick", days=7) is None and days.streak(db, "sick") == 0:
        return _("¡Bien! 💪")
    return _("¡Qué bueno! 💪 La primera sesión de vuelta baja ~10 % las cargas y deja 3 en reserva; "
             "si algo se siente raro, para.")


def parse_measurements(n: str) -> list[tuple[str, float]]:
    """`cintura 84, pecho 102.5` → [("waist_cm", 84.0), ("chest_cm", 102.5)]. The whole message must be
    measurements (so a set like `10x40` never matches)."""
    from ..body.service import MEASURE_WORDS

    out, rest = [], n
    for m in RE_MEASURE.finditer(n):
        kind = MEASURE_WORDS.get(m.group(1))
        if kind is None:
            return []
        out.append((kind, float(m.group(2).replace(",", "."))))
        rest = rest.replace(m.group(0), " ", 1)
    leftover = re.sub(r"\b(?:y|and)\b|[,;&+]", " ", rest).strip()
    return out if out and not leftover else []


def _log_measurements(db, items: list[tuple[str, float]]) -> str:
    from ..body import service as body
    from .cards import fmt_w

    parts, bad = [], []
    for kind, value in items:
        prev = body.latest_metric(db, kind)
        try:
            body.log_metric(db, kind, value)
        except ValueError:
            bad.append(f"{body.metric_label(kind).lower()} {fmt_w(value)}")
            continue
        unit = body.METRIC_UNITS.get(kind, "")
        txt = f"{body.metric_label(kind).lower()} {fmt_w(value)} {unit}"
        if prev is not None and prev.measured_on != body.local_today():
            d = round(value - prev.value, 1)
            txt += f" ({'+' if d > 0 else ''}{fmt_w(d)})"
        parts.append(txt)
    out = _("📏 Anotado: {items}", items=", ".join(parts)) if parts else ""
    if bad:
        out += ("\n" if out else "") + _("Esto no parece real: {items}", items=", ".join(bad))
    return out


async def handle_media(msg_id: int, caption: str, media: dict, wa_id: str | None) -> str:
    """An image in the group: a progress photo when it has no caption or a progress word."""
    from ..body import photos
    from .client import WahaError, get_client

    words = norm(caption)
    first = words.split(" ", 1)
    note = None
    if words and words not in PHOTO_WORDS:
        if first[0] in PHOTO_WORDS and len(first) > 1:
            note = caption.strip().split(" ", 1)[1]
        else:
            with session_scope() as db:
                enqueue_text(db, _("Por ahora guardo fotos de progreso: mándala sin texto o con *progreso*."),
                             handler="media")
            return "media_unknown"
    mt = (media.get("mimetype") or "").lower()
    if not media.get("url"):
        with session_scope() as db:
            enqueue_text(db, _("No pude bajar la foto: WhatsApp/WAHA no la descargó. Súbela en Progreso."),
                         handler="photo")
        return "photo_failed"
    if mt and not mt.startswith("image/"):
        with session_scope() as db:
            enqueue_text(db, _("Por ahora guardo fotos de progreso: mándala sin texto o con *progreso*."),
                         handler="media")
        return "media_unknown"
    try:
        data, ctype = await get_client().download(media["url"])
    except WahaError as e:
        log.warning("photo download failed: %s", e)
        with session_scope() as db:
            enqueue_text(db, _("No pude bajar la foto: WhatsApp/WAHA no la descargó. Súbela en Progreso."),
                         handler="photo")
        return "photo_failed"
    from ..core.timeutil import local_today

    with session_scope() as db:
        try:
            photos.save(db, data, mt or ctype, note=note, source="wa", wa_id=wa_id)
        except ValueError as e:
            enqueue_text(db, str(e), handler="photo")
            return "photo_failed"
        n = photos.count_since(db, local_today().replace(day=1))
        base = get_settings().portal_base_url.rstrip("/")
        enqueue_text(db, _("📸 Foto de progreso guardada ({n} este mes).", n=n)
                     + (f" {base}/progreso#fotos" if base else ""), handler="photo")
    return "photo"


async def handle_incoming(msg_id: int) -> None:
    media = None
    with session_scope() as db:
        msg = db.get(WaMessage, msg_id)
        if msg is None or msg.status != "received":
            return
        text = msg.body or ""
        n = norm(text)
        handler = None
        if msg.kind == "media":
            media, wa_id = dict(msg.media or {}), msg.wa_id
        elif n in ("ping",):
            enqueue_text(db, f"pong · Fit-OS {get_settings().git_sha[:7]}", handler="ping")
            handler = "ping"
        elif n in HELP_WORDS:
            enqueue_text(db, help_text(), handler="help")
            handler = "help"
        elif n in SICK | TRAVEL | INJURED:
            kind = "sick" if n in SICK else ("travel" if n in TRAVEL else "injured")
            enqueue_text(db, _mark_day(db, kind), handler=kind)
            handler = kind
        elif n in BETTER:
            enqueue_text(db, _better(db), handler="better")
            handler = "better"
        elif items := parse_measurements(n):
            enqueue_text(db, _log_measurements(db, items), handler="measure")
            handler = "measure"
        if handler:
            msg.status, msg.handler = "processed", handler
            return

    if media is not None:
        handler = await handle_media(msg_id, text, media, wa_id)
        with session_scope() as db:
            msg = db.get(WaMessage, msg_id)
            msg.status, msg.handler = "processed", handler
        return

    from .session_flow import handle_text  # noqa: PLC0415 — evita ciclo de imports

    handler = await handle_text(msg_id, text)
    with session_scope() as db:
        msg = db.get(WaMessage, msg_id)
        msg.status, msg.handler = "processed", handler
