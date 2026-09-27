from zoneinfo import ZoneInfo

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import create_async_engine

from digest.app import (
    StoredSchedule,
    default_schedules,
    nonstandard_schedule_alert,
    schedule_report_job,
    schedule_snapshot_jobs,
    startup_announcement,
)
from digest.config import AppConfig
from digest.delivery.ops import OpsChannel
from digest.mefi.client import MefiClient
from digest.reports.modules import IMPLEMENTED_MODULES
from digest.reports.periods import ReportLevel
from digest.reports.runner import ReportDeps
from factories import make_lead_links
from fakes import recording_bot


def test_startup_announcement_contains_version_and_dry_run_flag() -> None:
    assert startup_announcement("abc1234", dry_run=True) == (
        "Бот запущен, версия abc1234, DRY_RUN=1."
    )
    assert startup_announcement("abc1234", dry_run=False).endswith("DRY_RUN=0.")


async def test_report_and_snapshot_jobs_tolerate_late_start(app_config: AppConfig) -> None:
    report_bot, _ = recording_bot()
    ops_bot, _ = recording_bot()
    deps = ReportDeps(
        engine=create_async_engine("postgresql+asyncpg://unused/unused"),
        config=app_config,
        tenant_id="sofabelle",
        report_bot=report_bot,
        ops=OpsChannel(ops_bot, -1003),
        report_chat_id=-1001,
        modules=IMPLEMENTED_MODULES,
        lead_links=make_lead_links(app_config.status_mapping),
    )
    scheduler = AsyncIOScheduler(timezone=ZoneInfo("Europe/Bucharest"))
    async with httpx.AsyncClient() as http_client:
        schedule_snapshot_jobs(scheduler, deps, MefiClient(http_client))
    schedule_report_job(scheduler, deps, None, "daily", "30 19 * * *")

    grace_by_job = {job.id: job.misfire_grace_time for job in scheduler.get_jobs()}

    assert grace_by_job == {"snapshot_1900": 300, "snapshot_1910": 300, "report_daily": 1800}


def test_default_schedules_take_time_from_send_times(app_config: AppConfig) -> None:
    assert default_schedules(app_config) == {
        "daily": "30 19 * * *",
        "weekly": "0 9 * * mon",
        "monthly": "0 9 1 * *",
        "yearly": "0 9 5 1 *",
    }


def test_nonstandard_schedule_alert_names_levels_outside_send_times(
    app_config: AppConfig,
) -> None:
    schedules_by_level: dict[ReportLevel, StoredSchedule] = {
        "daily": StoredSchedule("*/5 19 * * *", enabled=True),
        "weekly": StoredSchedule("0 9 * * mon", enabled=True),
        "monthly": StoredSchedule("15 7 1 * *", enabled=False),
        "yearly": StoredSchedule("0 9 5 1 *", enabled=True),
    }

    alert = nonstandard_schedule_alert(app_config, schedules_by_level)

    assert alert is not None
    assert alert.startswith(
        "Расписание вне вариантов send_times: daily «*/5 19 * * *», monthly «15 7 1 * *»."
    )


def test_standard_schedules_give_no_alert(app_config: AppConfig) -> None:
    schedules_by_level = {
        level: StoredSchedule(cron, enabled=True)
        for level, cron in default_schedules(app_config).items()
    }

    assert nonstandard_schedule_alert(app_config, schedules_by_level) is None
