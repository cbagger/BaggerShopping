from datetime import datetime
from zoneinfo import ZoneInfo

from app.shopping_cleanup import seconds_until_next_midnight


def test_seconds_until_next_copenhagen_midnight():
    now = datetime(2026, 8, 13, 23, 59, 30, tzinfo=ZoneInfo("Europe/Copenhagen"))
    assert seconds_until_next_midnight(now) == 30


def test_seconds_until_midnight_handles_daylight_saving_timezone():
    now = datetime(2026, 1, 5, 12, 0, 0, tzinfo=ZoneInfo("Europe/Copenhagen"))
    assert seconds_until_next_midnight(now) == 12 * 60 * 60
