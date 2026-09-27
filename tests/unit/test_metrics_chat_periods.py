from datetime import date, datetime

import pytest

from digest.config import AppConfig
from digest.metrics.chat_periods import (
    CHAT_PERIODS,
    ChatPeriod,
    named_period_window,
    period_snapshot_date,
)
from digest.metrics.daily import daily_window
from digest.metrics.monthly import month_window
from digest.metrics.weekly import weekly_window
from factories import BUCHAREST

SUNDAY = date(2026, 9, 27)
WEDNESDAY = date(2026, 9, 23)


def at(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=BUCHAREST)


def test_today_window_is_daily_window(app_config: AppConfig) -> None:
    time_settings = app_config.status_mapping.time
    assert named_period_window("azi", WEDNESDAY, time_settings) == daily_window(
        WEDNESDAY, time_settings
    )


def test_yesterday_window_is_previous_daily_window(app_config: AppConfig) -> None:
    time_settings = app_config.status_mapping.time
    assert named_period_window("ieri", WEDNESDAY, time_settings) == daily_window(
        date(2026, 9, 22), time_settings
    )


def test_current_week_on_sunday_equals_weekly_window(app_config: AppConfig) -> None:
    time_settings = app_config.status_mapping.time
    assert named_period_window("saptamana_curenta", SUNDAY, time_settings) == weekly_window(
        SUNDAY, time_settings
    )


def test_current_week_midweek_starts_sunday_19_and_ends_today_19(app_config: AppConfig) -> None:
    window = named_period_window("saptamana_curenta", WEDNESDAY, app_config.status_mapping.time)
    assert (window.start, window.end) == (at(date(2026, 9, 20), 19), at(WEDNESDAY, 19))


@pytest.mark.parametrize("today", [date(2026, 9, 28), WEDNESDAY, SUNDAY])
def test_last_week_equals_weekly_window_of_last_sunday(app_config: AppConfig, today: date) -> None:
    time_settings = app_config.status_mapping.time
    last_sunday = date(2026, 9, 27) if today == date(2026, 9, 28) else date(2026, 9, 20)
    assert named_period_window("saptamana_trecuta", today, time_settings) == weekly_window(
        last_sunday, time_settings
    )


def test_last_month_equals_month_window(app_config: AppConfig) -> None:
    time_settings = app_config.status_mapping.time
    assert named_period_window("luna_trecuta", date(2026, 10, 1), time_settings) == month_window(
        date(2026, 9, 30), time_settings
    )


def test_current_month_starts_with_month_window(app_config: AppConfig) -> None:
    time_settings = app_config.status_mapping.time
    window = named_period_window("luna_curenta", WEDNESDAY, time_settings)
    assert window.start == month_window(WEDNESDAY, time_settings).start
    assert window.end == at(WEDNESDAY, 19)


def test_last_30_days_cross_dst_end_at_19_local(app_config: AppConfig) -> None:
    window = named_period_window(
        "ultimele_30_zile", date(2026, 11, 5), app_config.status_mapping.time
    )
    assert (window.start, window.end) == (at(date(2026, 10, 6), 19), at(date(2026, 11, 5), 19))
    assert window.end.utcoffset() != window.start.utcoffset()


@pytest.mark.parametrize(
    ("period", "expected"),
    [
        ("ieri", date(2026, 9, 22)),
        ("saptamana_trecuta", date(2026, 9, 20)),
        ("luna_trecuta", date(2026, 8, 31)),
        ("azi", date(2026, 9, 21)),
        ("saptamana_curenta", date(2026, 9, 21)),
        ("luna_curenta", date(2026, 9, 21)),
        ("ultimele_30_zile", date(2026, 9, 21)),
    ],
)
def test_closed_period_reads_snapshot_of_its_end_and_current_reads_latest(
    period: ChatPeriod, expected: date
) -> None:
    assert period_snapshot_date(period, WEDNESDAY, date(2026, 9, 21)) == expected


def test_every_period_has_a_window(app_config: AppConfig) -> None:
    for period in CHAT_PERIODS:
        window = named_period_window(period, WEDNESDAY, app_config.status_mapping.time)
        assert window.start < window.end
