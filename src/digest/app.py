import asyncio
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.backup_check import stale_backup_alert
from digest.config import AppConfig
from digest.db.schema import schedules
from digest.delivery.ops import OpsChannel, notify_ops
from digest.delivery.telegram import create_bot
from digest.mefi.client import MefiClient, create_mefi_http_client
from digest.reports.modules import IMPLEMENTED_MODULES
from digest.reports.periods import ReportLevel
from digest.reports.runner import ReportDeps, run_report
from digest.settings import Settings
from digest.snapshot import describe_error, run_daily_snapshot

logger = logging.getLogger(__name__)

# Бриф §4: дефолты при первом запуске, дальше расписание меняется только в таблице.
DEFAULT_SCHEDULES: dict[ReportLevel, str] = {
    "daily": "30 19 * * *",
    "weekly": "0 9 * * mon",
    "monthly": "0 9 1 * *",
    "yearly": "0 9 5 1 *",
}
SNAPSHOT_RETRY_DELAY = timedelta(minutes=10)


async def seed_defaults(engine: AsyncEngine, tenant_id: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            pg_insert(schedules)
            .values(
                [
                    {"tenant_id": tenant_id, "report_level": level, "cron": cron, "enabled": True}
                    for level, cron in DEFAULT_SCHEDULES.items()
                ]
            )
            .on_conflict_do_nothing()
        )


async def enabled_schedules(engine: AsyncEngine, tenant_id: str) -> dict[ReportLevel, str]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            select(schedules.c.report_level, schedules.c.cron).where(
                schedules.c.tenant_id == tenant_id, schedules.c.enabled
            )
        )
        return {TypeAdapter(ReportLevel).validate_python(level): cron for level, cron in rows}


def create_report_deps(
    engine: AsyncEngine, config: AppConfig, app_settings: Settings, dry_run: bool
) -> ReportDeps:
    # При dry-run продовый chat_id в зависимости не попадает: отправить в группу нечем.
    return ReportDeps(
        engine=engine,
        config=config,
        tenant_id=app_settings.tenant_id,
        report_bot=create_bot(app_settings.telegram_bot_token.get_secret_value()),
        ops=OpsChannel(
            create_bot(app_settings.telegram_ops_bot_token.get_secret_value()),
            app_settings.telegram_ops_chat_id,
        ),
        report_chat_id=app_settings.report_chat_id(dry_run),
        modules=IMPLEMENTED_MODULES,
    )


async def snapshot_job(deps: ReportDeps, mefi_client: MefiClient) -> None:
    now = datetime.now(ZoneInfo(deps.config.status_mapping.time.timezone))
    try:
        await run_daily_snapshot(
            deps.engine, mefi_client, deps.config.status_mapping, deps.tenant_id, now
        )
    except Exception as error:
        # str(error) может содержать тело ответа mefi или строки raw: наружу только describe_error.
        logger.error("snapshot failed", extra={"error": describe_error(error)})
        await notify_ops(
            deps.ops, f"Снапшот за {now:%d.%m.%Y %H:%M} не удался: {describe_error(error)}."
        )


async def report_job(deps: ReportDeps, level: ReportLevel, backup_dir: Path | None) -> None:
    now = datetime.now(ZoneInfo(deps.config.status_mapping.time.timezone))
    try:
        await run_report(level, now, deps)
    except Exception as error:
        logger.error("report job failed", extra={"level": level, "error": describe_error(error)})
        await notify_ops(deps.ops, f"Прогон отчёта {level} упал: {describe_error(error)}.")
    if level == "daily" and backup_dir is not None:
        try:
            backup_alert = stale_backup_alert(backup_dir, now)
        except Exception as error:
            logger.error("backup check failed", extra={"error": describe_error(error)})
            backup_alert = f"Проверка бэкапа упала: {describe_error(error)}."
        if backup_alert is not None:
            await notify_ops(deps.ops, backup_alert)


def startup_announcement(app_version: str, dry_run: bool) -> str:
    return f"Бот запущен, версия {app_version}, DRY_RUN={int(dry_run)}."


async def run_app(engine: AsyncEngine, config: AppConfig, app_settings: Settings) -> None:
    await seed_defaults(engine, app_settings.tenant_id)
    deps = create_report_deps(engine, config, app_settings, app_settings.dry_run)
    mefi_client = MefiClient(
        create_mefi_http_client(app_settings.mefi_base_url, app_settings.mefi_api_key)
    )
    time_settings = config.status_mapping.time
    timezone = ZoneInfo(time_settings.timezone)
    scheduler = AsyncIOScheduler(timezone=timezone)
    window_end = time_settings.daily_window_end
    retry_at = (datetime.combine(date.min, window_end) + SNAPSHOT_RETRY_DELAY).time()
    for attempt_at in (window_end, retry_at):
        scheduler.add_job(
            snapshot_job,
            CronTrigger(hour=attempt_at.hour, minute=attempt_at.minute, timezone=timezone),
            args=[deps, mefi_client],
            id=f"snapshot_{attempt_at:%H%M}",
        )
    for level, cron in (await enabled_schedules(engine, app_settings.tenant_id)).items():
        scheduler.add_job(
            report_job,
            CronTrigger.from_crontab(cron, timezone=timezone),
            args=[deps, level, app_settings.backup_dir],
            id=f"report_{level}",
        )
    scheduler.start()
    logger.info(
        "scheduler started",
        extra={"jobs": [job.id for job in scheduler.get_jobs()], "dry_run": app_settings.dry_run},
    )
    await notify_ops(deps.ops, startup_announcement(app_settings.app_version, app_settings.dry_run))
    await asyncio.Event().wait()
