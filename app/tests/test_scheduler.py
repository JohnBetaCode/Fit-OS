from datetime import datetime
from zoneinfo import ZoneInfo

from fitos.scheduler import JOBS, due_jobs

TZ = ZoneInfo("Etc/GMT+5")  # UTC-5, no daylight saving


def test_invalid_clock_runs_nothing():
    assert due_jobs(datetime(2000, 1, 1, 7, 0, tzinfo=TZ), {}, set()) == []


def test_daily_runs_once_after_time():
    now = datetime(2026, 11, 16, 6, 29, tzinfo=TZ)
    names = [j for j, _ in due_jobs(now, {"morning_hour": "06:30"}, set())]
    assert "morning_plan" not in names
    now = datetime(2026, 11, 16, 6, 31, tzinfo=TZ)
    due = due_jobs(now, {"morning_hour": "06:30"}, set())
    assert ("morning_plan", "2026-11-16") in due
    assert ("morning_plan", "2026-11-16") not in due_jobs(now, {"morning_hour": "06:30"}, set(due))


def test_disabled_daily():
    now = datetime(2026, 11, 16, 9, 0, tzinfo=TZ)
    names = [j for j, _ in due_jobs(now, {"morning_enabled": False}, set())]
    assert "morning_plan" not in names


def test_interval_period_keys():
    now = datetime(2026, 11, 16, 9, 7, tzinfo=TZ)
    keys = dict(due_jobs(now, {}, set(), [j for j in JOBS if j.name == "waha_check"]))
    assert keys["waha_check"] == "2026-11-16T109"
