from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from digest.config import AppConfig
from digest.metrics.kpi import Period
from digest.reports.periods import ReportLevel, report_period

BUCHAREST = ZoneInfo("Europe/Bucharest")


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
    app_config: AppConfig, level: ReportLevel, now: datetime, expected: Period
) -> None:
    assert report_period(level, now, app_config.status_mapping.time) == expected


def test_daily_window_across_dst_end_is_25_hours(app_config: AppConfig) -> None:
    period = report_period("daily", at(2026, 10, 25, 19, 30), app_config.status_mapping.time)

    assert period == Period(at(2026, 10, 24, 19), at(2026, 10, 25, 19))
    utc = ZoneInfo("UTC")
    duration = period.end.astimezone(utc) - period.start.astimezone(utc)
    assert duration.total_seconds() == 25 * 3600


def test_utc_now_is_read_in_bucharest_time(app_config: AppConfig) -> None:
    utc_now = datetime(2026, 9, 25, 16, 30, tzinfo=ZoneInfo("UTC"))

    period = report_period("daily", utc_now, app_config.status_mapping.time)

    assert period == Period(at(2026, 9, 24, 19), at(2026, 9, 25, 19))


def test_daily_window_across_dst_start_is_23_hours(app_config: AppConfig) -> None:
    period = report_period("daily", at(2026, 3, 29, 19, 30), app_config.status_mapping.time)

    assert period == Period(at(2026, 3, 28, 19), at(2026, 3, 29, 19))
    utc = ZoneInfo("UTC")
    duration = period.end.astimezone(utc) - period.start.astimezone(utc)
    assert duration.total_seconds() == 23 * 3600
