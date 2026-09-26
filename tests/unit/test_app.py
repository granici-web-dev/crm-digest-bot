from zoneinfo import ZoneInfo

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import create_async_engine

from digest.app import schedule_report_job, schedule_snapshot_jobs, startup_announcement
from digest.config import AppConfig
from digest.delivery.ops import OpsChannel
from digest.mefi.client import MefiClient
from digest.reports.modules import IMPLEMENTED_MODULES
from digest.reports.runner import ReportDeps
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
    )
    scheduler = AsyncIOScheduler(timezone=ZoneInfo("Europe/Bucharest"))
    async with httpx.AsyncClient() as http_client:
        schedule_snapshot_jobs(scheduler, deps, MefiClient(http_client))
    schedule_report_job(scheduler, deps, None, "daily", "30 19 * * *")

    grace_by_job = {job.id: job.misfire_grace_time for job in scheduler.get_jobs()}

    assert grace_by_job == {"snapshot_1900": 300, "snapshot_1910": 300, "report_daily": 1800}
