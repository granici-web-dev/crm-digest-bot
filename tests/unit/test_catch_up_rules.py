from datetime import date, datetime

import pytest

from digest.catch_up import late_snapshot_due, missed_snapshot_alert, report_catch_up_due
from digest.config import AppConfig, SettingsLevel
from digest.reports.periods import report_period
from factories import BUCHAREST

DEFAULT_CRONS: dict[SettingsLevel, str] = {
    "daily": "30 19 * * *",
    "weekly": "0 9 * * mon",
    "monthly": "0 9 1 * *",
}


def due(
    app_config: AppConfig, level: SettingsLevel, now: datetime, cron: str | None = None
) -> bool:
    time_settings = app_config.status_mapping.time
    return report_catch_up_due(
        cron or DEFAULT_CRONS[level],
        report_period(level, now, time_settings),
        now,
        app_config.modules.catch_up_days[level],
        BUCHAREST,
    )


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [(18, 59, False), (19, 5, False), (19, 9, False), (19, 10, True), (23, 59, True)],
)
def test_late_snapshot_is_due_only_after_scheduled_retry(
    app_config: AppConfig, hour: int, minute: int, expected: bool
) -> None:
    now = datetime(2026, 9, 28, hour, minute, tzinfo=BUCHAREST)

    assert late_snapshot_due(now, app_config.status_mapping.time) is expected


def test_late_snapshot_is_not_due_after_midnight(app_config: AppConfig) -> None:
    now = datetime(2026, 9, 29, 0, 30, tzinfo=BUCHAREST)

    assert late_snapshot_due(now, app_config.status_mapping.time) is False


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 9, 28, 8, 59, tzinfo=BUCHAREST), False),
        (datetime(2026, 9, 28, 9, 0, tzinfo=BUCHAREST), True),
        (datetime(2026, 9, 30, 23, 59, tzinfo=BUCHAREST), True),
        (datetime(2026, 10, 1, 0, 1, tzinfo=BUCHAREST), False),
        (datetime(2026, 10, 4, 12, 0, tzinfo=BUCHAREST), False),
    ],
)
def test_weekly_catch_up_lasts_catch_up_days_after_monday(
    app_config: AppConfig, now: datetime, expected: bool
) -> None:
    assert due(app_config, "weekly", now) is expected


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 10, 1, 8, 59, tzinfo=BUCHAREST), False),
        (datetime(2026, 10, 1, 9, 0, tzinfo=BUCHAREST), True),
        (datetime(2026, 10, 5, 23, 0, tzinfo=BUCHAREST), True),
        (datetime(2026, 10, 6, 10, 0, tzinfo=BUCHAREST), False),
    ],
)
def test_monthly_catch_up_lasts_catch_up_days_after_first(
    app_config: AppConfig, now: datetime, expected: bool
) -> None:
    assert due(app_config, "monthly", now) is expected


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 9, 28, 19, 29, tzinfo=BUCHAREST), False),
        (datetime(2026, 9, 28, 19, 31, tzinfo=BUCHAREST), True),
        (datetime(2026, 9, 28, 23, 59, tzinfo=BUCHAREST), True),
        (datetime(2026, 9, 29, 0, 30, tzinfo=BUCHAREST), False),
        (datetime(2026, 9, 29, 18, 0, tzinfo=BUCHAREST), False),
        (datetime(2026, 10, 25, 19, 31, tzinfo=BUCHAREST), True),
    ],
)
def test_daily_catch_up_only_on_the_same_day(
    app_config: AppConfig, now: datetime, expected: bool
) -> None:
    assert due(app_config, "daily", now) is expected


def test_catch_up_follows_schedule_cron_not_default(app_config: AppConfig) -> None:
    now = datetime(2026, 9, 28, 8, 30, tzinfo=BUCHAREST)

    assert due(app_config, "weekly", now, cron="0 8 * * mon") is True
    assert due(app_config, "weekly", now) is False


def test_missed_snapshot_alert_names_every_date() -> None:
    assert missed_snapshot_alert([date(2026, 9, 26)]) == (
        "Снапшот за 26.09.2026 пропущен, данные дня не восстановить."
    )
    assert missed_snapshot_alert([date(2026, 9, 26), date(2026, 9, 27)]) == (
        "Снапшоты за 26.09.2026, 27.09.2026 пропущены, данные этих дней не восстановить."
    )
