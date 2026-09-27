import asyncio
import logging
import signal
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

from anthropic import AsyncAnthropic
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pydantic import TypeAdapter
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.backup_check import stale_backup_alert
from digest.bot.chat_handlers import ChatDeps
from digest.bot.dispatcher import bot_dispatcher
from digest.bot.polling import PollingHealth, supervise_polling, watch_polling_silence
from digest.bot.settings_menu import standard_send_time
from digest.config import SETTINGS_LEVELS, SNAPSHOT_RETRY_DELAY, AppConfig, SettingsLevel
from digest.db.schema import schedules
from digest.delivery.ops import OpsChannel, notify_ops
from digest.delivery.telegram import create_bot
from digest.mefi.client import MefiClient, create_mefi_http_client
from digest.reports.lead_links import LeadLinks
from digest.reports.modules import IMPLEMENTED_MODULES
from digest.reports.periods import ReportLevel
from digest.reports.runner import ReportDeps, run_report
from digest.settings import Settings
from digest.snapshot import describe_error, run_daily_snapshot

logger = logging.getLogger(__name__)

CHAT_API_TIMEOUT_SECONDS = 30

# Отчёт, опоздавший до получаса, руководству ещё полезен; позже его отправляют вручную.
REPORT_MISFIRE_GRACE = timedelta(minutes=30)
# Снапшот позже этого наезжал бы на повтор через SNAPSHOT_RETRY_DELAY.
SNAPSHOT_MISFIRE_GRACE = timedelta(minutes=5)
POLLING_FIRST_RETRY_DELAY = timedelta(seconds=5)
POLLING_MAX_RETRY_DELAY = timedelta(minutes=10)
POLLING_SILENCE_CHECK_INTERVAL = timedelta(minutes=1)
POLLING_SILENCE_ALERT_AFTER = timedelta(minutes=30)

# Бриф §4: дефолты при первом запуске, дальше расписание меняется только в таблице.
# Время уровней /settings берётся из send_times, здесь только дни.
DEFAULT_SCHEDULE_DAYS: dict[SettingsLevel, str] = {
    "daily": "* * *",
    "weekly": "* * mon",
    "monthly": "1 * *",
}
YEARLY_DEFAULT_SCHEDULE = "0 9 5 1 *"


class StoredSchedule(NamedTuple):
    cron: str
    enabled: bool


def default_schedules(config: AppConfig) -> dict[ReportLevel, str]:
    crons: dict[ReportLevel, str] = {"yearly": YEARLY_DEFAULT_SCHEDULE}
    for level in SETTINGS_LEVELS:
        send_time = config.modules.default_send_time(level)
        crons[level] = f"{send_time.minute} {send_time.hour} {DEFAULT_SCHEDULE_DAYS[level]}"
    return crons


async def seed_defaults(engine: AsyncEngine, config: AppConfig, tenant_id: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            pg_insert(schedules)
            .values(
                [
                    {"tenant_id": tenant_id, "report_level": level, "cron": cron, "enabled": True}
                    for level, cron in default_schedules(config).items()
                ]
            )
            .on_conflict_do_nothing()
        )


async def stored_schedules(
    engine: AsyncEngine, tenant_id: str
) -> dict[ReportLevel, StoredSchedule]:
    async with engine.connect() as connection:
        rows = await connection.execute(
            select(schedules.c.report_level, schedules.c.cron, schedules.c.enabled).where(
                schedules.c.tenant_id == tenant_id
            )
        )
        return {
            TypeAdapter(ReportLevel).validate_python(level): StoredSchedule(cron, enabled)
            for level, cron, enabled in rows
        }


def nonstandard_schedule_alert(
    config: AppConfig, schedules_by_level: Mapping[ReportLevel, StoredSchedule]
) -> str | None:
    nonstandard = [
        f"{level} «{schedules_by_level[level].cron}»"
        for level in SETTINGS_LEVELS
        if level in schedules_by_level
        and standard_send_time(config, level, schedules_by_level[level].cron) is None
    ]
    if not nonstandard:
        return None
    return (
        f"Расписание вне вариантов send_times: {', '.join(nonstandard)}. /settings показывает "
        "«program nestandard» и время не меняет: поправить cron в schedules или send_times."
    )


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
        lead_links=LeadLinks.from_mefi_base_url(
            app_settings.mefi_base_url, config.status_mapping.lead_links
        ),
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


def schedule_report_job(
    scheduler: AsyncIOScheduler,
    deps: ReportDeps,
    backup_dir: Path | None,
    level: ReportLevel,
    cron: str,
) -> None:
    scheduler.add_job(
        report_job,
        CronTrigger.from_crontab(cron, timezone=ZoneInfo(deps.config.status_mapping.time.timezone)),
        args=[deps, level, backup_dir],
        id=f"report_{level}",
        replace_existing=True,
        misfire_grace_time=int(REPORT_MISFIRE_GRACE.total_seconds()),
    )


def schedule_snapshot_jobs(
    scheduler: AsyncIOScheduler, deps: ReportDeps, mefi_client: MefiClient
) -> None:
    time_settings = deps.config.status_mapping.time
    timezone = ZoneInfo(time_settings.timezone)
    window_end = time_settings.daily_window_end
    retry_at = (datetime.combine(date.min, window_end) + SNAPSHOT_RETRY_DELAY).time()
    for attempt_at in (window_end, retry_at):
        scheduler.add_job(
            snapshot_job,
            CronTrigger(hour=attempt_at.hour, minute=attempt_at.minute, timezone=timezone),
            args=[deps, mefi_client],
            id=f"snapshot_{attempt_at:%H%M}",
            misfire_grace_time=int(SNAPSHOT_MISFIRE_GRACE.total_seconds()),
        )


def startup_announcement(app_version: str, dry_run: bool) -> str:
    return f"Бот запущен, версия {app_version}, DRY_RUN={int(dry_run)}."


def create_anthropic_client(app_settings: Settings) -> AsyncAnthropic | None:
    if app_settings.anthropic_api_key is None:
        return None
    return AsyncAnthropic(
        api_key=app_settings.anthropic_api_key.get_secret_value(),
        timeout=CHAT_API_TIMEOUT_SECONDS,
        max_retries=2,
    )


def chat_ids(app_settings: Settings) -> frozenset[int]:
    # Продовая группа только без DRY_RUN: вопросы в ней не должны получать ответы тестового бота.
    if app_settings.dry_run:
        return frozenset({app_settings.telegram_test_chat_id})
    return frozenset({app_settings.telegram_test_chat_id, app_settings.telegram_group_chat_id})


async def run_app(engine: AsyncEngine, config: AppConfig, app_settings: Settings) -> None:
    await seed_defaults(engine, config, app_settings.tenant_id)
    deps = create_report_deps(engine, config, app_settings, app_settings.dry_run)
    mefi_http_client = create_mefi_http_client(
        app_settings.mefi_base_url, app_settings.mefi_api_key
    )
    mefi_client = MefiClient(mefi_http_client)
    scheduler = AsyncIOScheduler(timezone=ZoneInfo(config.status_mapping.time.timezone))
    schedule_snapshot_jobs(scheduler, deps, mefi_client)
    reschedule = partial(schedule_report_job, scheduler, deps, app_settings.backup_dir)
    schedules_by_level = await stored_schedules(engine, app_settings.tenant_id)
    for level, schedule in schedules_by_level.items():
        if schedule.enabled:
            reschedule(level, schedule.cron)
    scheduler.start()
    logger.info(
        "scheduler started",
        extra={"jobs": [job.id for job in scheduler.get_jobs()], "dry_run": app_settings.dry_run},
    )
    await notify_ops(deps.ops, startup_announcement(app_settings.app_version, app_settings.dry_run))
    schedule_alert = nonstandard_schedule_alert(config, schedules_by_level)
    if schedule_alert is not None:
        await notify_ops(deps.ops, schedule_alert)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for stop_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(stop_signal, stop.set)
    health = PollingHealth()
    deps.report_bot.session.middleware(health)
    anthropic_client = create_anthropic_client(app_settings)
    dispatcher = bot_dispatcher(
        deps,
        reschedule,
        app_settings.telegram_admin_ids,
        clock=partial(datetime.now, ZoneInfo(config.status_mapping.time.timezone)),
        chat=ChatDeps(anthropic_client, app_settings.anthropic_model, chat_ids(app_settings)),
    )
    start_polling = partial(
        dispatcher.start_polling,
        deps.report_bot,
        allowed_updates=["message", "callback_query"],
        handle_signals=False,
        # Сессию бота отчётов делит раннер: перезапуск polling не должен её закрывать.
        close_bot_session=False,
    )
    polling_tasks = [
        asyncio.create_task(
            supervise_polling(
                start_polling,
                health,
                deps.ops,
                stop,
                POLLING_FIRST_RETRY_DELAY,
                POLLING_MAX_RETRY_DELAY,
            )
        ),
        asyncio.create_task(
            watch_polling_silence(
                health,
                deps.ops,
                stop,
                POLLING_SILENCE_CHECK_INTERVAL,
                POLLING_SILENCE_ALERT_AFTER,
            )
        ),
    ]
    await stop.wait()
    logger.info("shutdown requested")
    scheduler.shutdown(wait=False)
    for task in polling_tasks:
        task.cancel()
    await asyncio.gather(*polling_tasks, return_exceptions=True)
    await mefi_http_client.aclose()
    if anthropic_client is not None:
        await anthropic_client.close()
    await deps.report_bot.session.close()
    await deps.ops.bot.session.close()
