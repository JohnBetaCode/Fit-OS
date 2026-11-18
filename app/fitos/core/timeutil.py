"""Tiempo: UTC en la base, la zona `TZ` para fechas de calendario. Hosts sin RTC (placas ARM): tras un corte creen que es
el año 2000 hasta que NTP sincroniza, así que nada programado corre con un reloj anterior a 2026."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from .settings import get_settings

MIN_VALID_YEAR = 2026


def utcnow() -> datetime:
    return datetime.now(UTC)


def local_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().tz)


def to_local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(local_tz())


def local_now() -> datetime:
    return to_local(utcnow())


def local_today() -> date:
    return local_now().date()


def clock_is_valid(now: datetime | None = None) -> bool:
    return (now or utcnow()).year >= MIN_VALID_YEAR


def age_on(birth: date, on: date) -> int:
    return on.year - birth.year - ((on.month, on.day) < (birth.month, birth.day))


# Calendar labels in both UI languages. Format everywhere: weekday, day, month ("dom 15 nov" / "Sun 15 Nov").
DAYS_ES = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
DAYS_EN = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAYS_LONG_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
DAYS_LONG_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_LETTERS = {"es": "LMXJVSD", "en": "MTWTFSS"}
MONTHS_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _lang(code: str | None) -> str:
    if code in ("es", "en"):
        return code
    from ..i18n import lang  # noqa: PLC0415 — i18n reads prefs, which import this module's siblings

    return lang()


def weekday_short(d: date | int, code: str | None = None) -> str:
    """'dom' / 'Sun'. Accepts a date or a weekday index (Monday = 0)."""
    i = d if isinstance(d, int) else d.weekday()
    return (DAYS_EN if _lang(code) == "en" else DAYS_ES)[i]


def weekday_long(d: date | int | str, code: str | None = None) -> str:
    """'Domingo' / 'Sunday'. Accepts a date, an index (Monday = 0) or a key ('sun')."""
    i = WEEKDAY_KEYS.index(d) if isinstance(d, str) else (d if isinstance(d, int) else d.weekday())
    return (DAYS_LONG_EN if _lang(code) == "en" else DAYS_LONG_ES)[i].capitalize()


def day_letters(code: str | None = None) -> str:
    """One letter per weekday, Monday first: 'LMXJVSD' / 'MTWTFSS'."""
    return DAY_LETTERS[_lang(code)]


def month_short(m: date | int, code: str | None = None) -> str:
    """'nov' / 'Nov'. Accepts a date or a month number (1-12)."""
    n = m if isinstance(m, int) else m.month
    return (MONTHS_EN if _lang(code) == "en" else MONTHS_ES)[n - 1]


def months_short(code: str | None = None) -> list[str]:
    return list(MONTHS_EN if _lang(code) == "en" else MONTHS_ES)


def fmt_date(d: date, code: str | None = None) -> str:
    """'dom 15 nov' in Spanish, 'Sun 15 Nov' in English (current language unless `code` is given)."""
    return f"{weekday_short(d, code)} {d.day} {month_short(d, code)}"


def fmt_day_month(d: date, code: str | None = None) -> str:
    """'15 nov' / '15 Nov'."""
    return f"{d.day} {month_short(d, code)}"


def fmt_month_year(d: date, code: str | None = None) -> str:
    """'nov 2026' / 'Nov 2026'."""
    return f"{month_short(d, code)} {d.year}"


def fmt_date_es(d: date) -> str:
    """Kept for existing callers: now follows the current language like `fmt_date`."""
    return fmt_date(d)
