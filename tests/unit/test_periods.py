from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from digest.metrics.kpi import Period
from digest.reports.periods import ReportLevel, report_period

BUCHAREST = ZoneInfo("Europe/Bucharest")
DAILY_END = time(19, 0)


def at(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=BUCHAREST)


@pytest.mark.parametrize(
    ("level", "now", "expected"),
    [
        ("daily", at(2026, 9, 25, 19, 30), Period(at(2026, 9, 24, 19), at(2026, 9, 25, 19))),
        ("daily", at(2026, 9, 25, 19, 0), Period(at(2026, 9, 24, 19), at(2026, 9, 25, 19))),
        ("daily", at(2026, 9, 25, 18, 59), Period(at(2026, 9, 23, 19), at(2026, 9, 24, 19))),
        ("weekly", at(2026, 9, 28, 9, 0), Period(at(2026, 9, 21), at(2026, 9, 28))),
        ("weekly", at(2026, 10, 1, 12, 0), Period(at(2026, 9, 21), at(2026, 9, 28))),
        ("monthly", at(2026, 10, 1, 9, 0), Period(at(2026, 9, 1), at(2026, 10, 1))),
        ("monthly", at(2027, 1, 1, 9, 0), Period(at(2026, 12, 1), at(2027, 1, 1))),
        ("yearly", at(2027, 1, 5, 9, 0), Period(at(2026, 1, 1), at(2027, 1, 1))),
    ],
)
def test_period_is_last_complete_one_before_now(
    level: ReportLevel, now: datetime, expected: Period
) -> None:
    assert report_period(level, now, BUCHAREST, DAILY_END) == expected


def test_daily_window_across_dst_end_is_25_hours() -> None:
    period = report_period("daily", at(2026, 10, 25, 19, 30), BUCHAREST, DAILY_END)

    assert period == Period(at(2026, 10, 24, 19), at(2026, 10, 25, 19))
    utc = ZoneInfo("UTC")
    duration = period.end.astimezone(utc) - period.start.astimezone(utc)
    assert duration.total_seconds() == 25 * 3600


def test_utc_now_is_read_in_bucharest_time() -> None:
    utc_now = datetime(2026, 9, 25, 16, 30, tzinfo=ZoneInfo("UTC"))

    period = report_period("daily", utc_now, BUCHAREST, DAILY_END)

    assert period == Period(at(2026, 9, 24, 19), at(2026, 9, 25, 19))
