from datetime import date, datetime

import pytest

from digest.config import AppConfig
from digest.metrics.chat_periods import (
    CHAT_PERIODS,
    ChatDay,
    ChatMonth,
    ChatPeriod,
    chat_period_window,
    earliest_specific_day,
    first_snapshot_on_or_after,
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
    assert chat_period_window("azi", WEDNESDAY, time_settings) == daily_window(
        WEDNESDAY, time_settings
    )


def test_yesterday_window_is_previous_daily_window(app_config: AppConfig) -> None:
    time_settings = app_config.status_mapping.time
    assert chat_period_window("ieri", WEDNESDAY, time_settings) == daily_window(
        date(2026, 9, 22), time_settings
    )


def test_current_week_on_sunday_equals_weekly_window(app_config: AppConfig) -> None:
    time_settings = app_config.status_mapping.time
    assert chat_period_window("saptamana_curenta", SUNDAY, time_settings) == weekly_window(
        SUNDAY, time_settings
    )


def test_current_week_midweek_starts_sunday_19_and_ends_today_19(app_config: AppConfig) -> None:
    window = chat_period_window("saptamana_curenta", WEDNESDAY, app_config.status_mapping.time)
    assert (window.start, window.end) == (at(date(2026, 9, 20), 19), at(WEDNESDAY, 19))


@pytest.mark.parametrize("today", [date(2026, 9, 28), WEDNESDAY, SUNDAY])
def test_last_week_equals_weekly_window_of_last_sunday(app_config: AppConfig, today: date) -> None:
    time_settings = app_config.status_mapping.time
    last_sunday = date(2026, 9, 27) if today == date(2026, 9, 28) else date(2026, 9, 20)
    assert chat_period_window("saptamana_trecuta", today, time_settings) == weekly_window(
        last_sunday, time_settings
    )


def test_last_month_equals_month_window(app_config: AppConfig) -> None:
    time_settings = app_config.status_mapping.time
    assert chat_period_window("luna_trecuta", date(2026, 10, 1), time_settings) == month_window(
        date(2026, 9, 30), time_settings
    )


def test_current_month_starts_with_month_window(app_config: AppConfig) -> None:
    time_settings = app_config.status_mapping.time
    window = chat_period_window("luna_curenta", WEDNESDAY, time_settings)
    assert window.start == month_window(WEDNESDAY, time_settings).start
    assert window.end == at(WEDNESDAY, 19)


def test_last_30_days_cross_dst_end_at_19_local(app_config: AppConfig) -> None:
    window = chat_period_window(
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
        ("azi", date(2026, 9, 22)),
        ("saptamana_curenta", date(2026, 9, 22)),
        ("luna_curenta", date(2026, 9, 22)),
        ("ultimele_30_zile", date(2026, 9, 22)),
    ],
)
def test_closed_period_reads_snapshot_of_its_end_and_current_reads_latest(
    period: ChatPeriod, expected: date
) -> None:
    dates = (date(2026, 8, 31), date(2026, 9, 20), date(2026, 9, 21), date(2026, 9, 22))
    assert period_snapshot_date(period, WEDNESDAY, dates) == expected


def test_closed_period_uses_snapshot_of_its_last_day_when_present() -> None:
    dates = (date(2026, 8, 30), date(2026, 8, 31), date(2026, 9, 25))
    assert period_snapshot_date("luna_trecuta", WEDNESDAY, dates) == date(2026, 8, 31)


def test_closed_period_without_last_day_snapshot_uses_first_later_one() -> None:
    dates = (date(2026, 8, 30), date(2026, 9, 25), date(2026, 9, 26))
    assert period_snapshot_date("luna_trecuta", WEDNESDAY, dates) == date(2026, 9, 25)


def test_closed_period_without_last_day_or_later_snapshot_has_none() -> None:
    dates = (date(2026, 8, 29), date(2026, 8, 30))
    assert period_snapshot_date("luna_trecuta", WEDNESDAY, dates) is None


@pytest.mark.parametrize(
    ("until", "expected"),
    [(None, date(2026, 9, 25)), (date(2026, 9, 26), date(2026, 9, 25)), (date(2026, 9, 25), None)],
)
def test_first_snapshot_on_or_after_excludes_until(
    until: date | None, expected: date | None
) -> None:
    dates = (date(2026, 9, 18), date(2026, 9, 25), date(2026, 9, 28))
    assert first_snapshot_on_or_after(date(2026, 9, 20), dates, until) == expected


def test_every_period_has_a_window(app_config: AppConfig) -> None:
    for period in CHAT_PERIODS:
        window = chat_period_window(period, WEDNESDAY, app_config.status_mapping.time)
        assert window.start < window.end


def test_specific_day_window_equals_daily_window(app_config: AppConfig) -> None:
    time_settings = app_config.status_mapping.time
    assert chat_period_window(ChatDay(date(2026, 9, 25)), SUNDAY, time_settings) == daily_window(
        date(2026, 9, 25), time_settings
    )


@pytest.mark.parametrize("first_day", [date(2026, 8, 1), date(2026, 10, 1), date(2026, 2, 1)])
def test_specific_month_window_equals_month_window(app_config: AppConfig, first_day: date) -> None:
    time_settings = app_config.status_mapping.time
    window = chat_period_window(ChatMonth(first_day), date(2026, 11, 5), time_settings)
    assert window == month_window(first_day, time_settings)


def test_specific_day_reads_its_own_snapshot() -> None:
    dates = (date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 26))
    assert period_snapshot_date(ChatDay(date(2026, 9, 25)), SUNDAY, dates) == date(2026, 9, 25)


def test_specific_month_without_end_snapshot_takes_first_later() -> None:
    dates = (date(2026, 7, 30), date(2026, 9, 2), date(2026, 9, 26))
    assert period_snapshot_date(ChatMonth(date(2026, 7, 1)), SUNDAY, dates) == date(2026, 9, 2)


def test_specific_month_without_any_later_snapshot_has_none() -> None:
    dates = (date(2026, 7, 29), date(2026, 7, 30))
    assert period_snapshot_date(ChatMonth(date(2026, 7, 1)), SUNDAY, dates) is None


@pytest.mark.parametrize(
    ("first_snapshot", "expected"),
    [(date(2026, 9, 24), date(2025, 9, 24)), (date(2028, 2, 29), date(2027, 2, 28))],
)
def test_earliest_specific_day_is_a_year_before_first_snapshot(
    first_snapshot: date, expected: date
) -> None:
    assert earliest_specific_day(first_snapshot, 1) == expected
