import asyncio
import logging
import signal
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
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
from digest.catch_up import late_snapshot_due, missed_snapshot_alert, report_catch_up_due
from digest.config import (
    FINAL_SNAPSHOT_CHECK_DELAY,
    SETTINGS_LEVELS,
    AppConfig,
    SettingsLevel,
    TimeSettings,
)
from digest.db.lead_frame import previous_success_snapshot_date
from digest.db.schema import schedules
from digest.delivery.ops import OpsChannel, notify_ops
from digest.delivery.telegram import create_bot
from digest.mefi.client import MefiClient, create_mefi_http_client
from digest.reports.lead_links import LeadLinks
from digest.reports.modules import IMPLEMENTED_MODULES
from digest.reports.periods import ReportLevel, report_period
from digest.reports.runner import ReportDeps, run_report, select_modules
from digest.settings import Settings
from digest.snapshot import (
    SnapshotOutcome,
    SnapshotRunInProgress,
    SnapshotSources,
    SnapshotTrigger,
    dates_without_success_snapshot,
    describe_error,
    record_missed_snapshot_dates,
    run_daily_snapshot,
    snapshot_run_in_progress,
)

logger = logging.getLogger(__name__)

CHAT_API_TIMEOUT_SECONDS = 30
CHAT_API_MAX_RETRIES = 2

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


async def alert_failure(
    deps: ReportDeps, log_message: str, ops_text: str, error: BaseException, **log_extra: object
) -> None:
    # str(error) может содержать тело ответа mefi или строки raw: наружу только describe_error.
    description = describe_error(error)
    logger.error(log_message, extra={**log_extra, "error": description})
    await notify_ops(deps.ops, f"{ops_text}: {description}.")


async def take_snapshot(
    deps: ReportDeps, snapshot_sources: SnapshotSources, now: datetime, trigger: SnapshotTrigger
) -> SnapshotOutcome | None:
    try:
        outcome = await run_daily_snapshot(
            deps.engine,
            snapshot_sources,
            deps.config.status_mapping,
            deps.tenant_id,
            now,
            trigger,
        )
    except SnapshotRunInProgress:
        logger.info("snapshot skipped, run in progress", extra={"trigger": trigger})
        return None
    except Exception as error:
        await alert_failure(
            deps, "snapshot failed", f"Снапшот за {now:%d.%m.%Y %H:%M} не удался", error
        )
        return None
    if outcome.clients_alert is not None:
        await notify_ops(deps.ops, outcome.clients_alert)
    return outcome


async def snapshot_job(
    deps: ReportDeps, snapshot_sources: SnapshotSources, trigger: SnapshotTrigger
) -> None:
    now = datetime.now(ZoneInfo(deps.config.status_mapping.time.timezone))
    await take_snapshot(deps, snapshot_sources, now, trigger)


async def missing_final_snapshot_alert(
    engine: AsyncEngine, tenant_id: str, now: datetime, time_settings: TimeSettings
) -> str | None:
    today = now.date()
    if not await dates_without_success_snapshot(engine, tenant_id, [today]):
        return None
    # Поздний снапшот ещё идёт: совет снять вручную вытеснил бы его второй выгрузкой.
    async with engine.connect() as connection:
        if await snapshot_run_in_progress(connection, tenant_id, today, now, time_settings):
            return None
    return (
        f"Нет финального снапшота mefi за {now:%d.%m.%Y}: отчёты за этот день "
        "не построятся. До полуночи его можно снять вручную: python -m digest snapshot."
    )


async def final_snapshot_check_job(deps: ReportDeps) -> None:
    # Сбой джобы 19:00 алертит сама джоба; эта проверка ловит день, когда процесс лежал
    # и джоба не запускалась вовсе. Прошедшие даты закрывает report_missed_snapshots.
    now = datetime.now(ZoneInfo(deps.config.status_mapping.time.timezone))
    try:
        alert = await missing_final_snapshot_alert(
            deps.engine, deps.tenant_id, now, deps.config.status_mapping.time
        )
    except Exception as error:
        await alert_failure(
            deps, "final snapshot check failed", "Проверка финального снапшота упала", error
        )
        return
    if alert is not None:
        await notify_ops(deps.ops, alert)


async def report_missed_snapshots(deps: ReportDeps, today: date, trigger: SnapshotTrigger) -> None:
    try:
        last_success = await previous_success_snapshot_date(deps.engine, deps.tenant_id, today)
        # До первого снапшота пропускать нечего: свежая база не должна алертить о прошлом.
        if last_success is None:
            return
        gap_dates = [
            last_success + timedelta(days=offset)
            for offset in range(1, (today - last_success).days)
        ]
        missed = await record_missed_snapshot_dates(deps.engine, deps.tenant_id, gap_dates, trigger)
    except Exception as error:
        await alert_failure(
            deps, "missed snapshot check failed", "Проверка пропущенных снапшотов упала", error
        )
        return
    if missed:
        await notify_ops(deps.ops, missed_snapshot_alert(missed))


async def missed_snapshot_job(deps: ReportDeps) -> None:
    now = datetime.now(ZoneInfo(deps.config.status_mapping.time.timezone))
    await report_missed_snapshots(deps, now.date(), "scheduled")


async def take_late_snapshot(
    deps: ReportDeps, snapshot_sources: SnapshotSources, now: datetime
) -> None:
    time_settings = deps.config.status_mapping.time
    local_now = now.astimezone(ZoneInfo(time_settings.timezone))
    # После полуночи дата уже другая: вчерашний снапшот не снять, его закроет проверка пропусков.
    if not late_snapshot_due(local_now, time_settings):
        return
    today = local_now.date()
    if not await dates_without_success_snapshot(deps.engine, deps.tenant_id, [today]):
        return
    outcome = await take_snapshot(deps, snapshot_sources, local_now, "catch_up")
    if outcome is not None and outcome.newly_taken and outcome.status == "success":
        await notify_ops(
            deps.ops, f"Снапшот за {today:%d.%m.%Y} снят с опозданием в {local_now:%H:%M}."
        )


async def catch_up_level(
    deps: ReportDeps, level: SettingsLevel, schedule: StoredSchedule, now: datetime
) -> None:
    time_settings = deps.config.status_mapping.time
    period = report_period(level, now, time_settings)
    catch_up_days = deps.config.modules.catch_up_days.for_level(level)
    timezone = ZoneInfo(time_settings.timezone)
    if not report_catch_up_due(schedule.cron, period, now, catch_up_days, timezone):
        return
    # Без модулей раннер писал бы failed «no modules» и алертил на каждом рестарте.
    if not (await select_modules(deps.reader, level)).runnable:
        return
    # Без сегодняшнего снапшота ежедневный отчёт состоял бы из одной пометки «нет данных».
    if level == "daily" and await dates_without_success_snapshot(
        deps.engine, deps.tenant_id, [now.date()]
    ):
        logger.info("daily catch-up skipped, no snapshot", extra={"date": now.date().isoformat()})
        return
    await run_report(level, now, deps, late=True)


async def catch_up_on_startup(
    deps: ReportDeps, now: datetime, schedules_by_level: Mapping[ReportLevel, StoredSchedule]
) -> None:
    local_now = now.astimezone(ZoneInfo(deps.config.status_mapping.time.timezone))
    await report_missed_snapshots(deps, local_now.date(), "catch_up")
    for level in SETTINGS_LEVELS:
        schedule = schedules_by_level.get(level)
        if schedule is None or not schedule.enabled:
            continue
        try:
            await catch_up_level(deps, level, schedule, local_now)
        except Exception as error:
            await alert_failure(
                deps, "report catch-up failed", f"Догон отчёта {level} упал", error, level=level
            )


async def report_job(deps: ReportDeps, level: ReportLevel, backup_dir: Path | None) -> None:
    now = datetime.now(ZoneInfo(deps.config.status_mapping.time.timezone))
    try:
        await run_report(level, now, deps, late=False)
    except Exception as error:
        await alert_failure(
            deps, "report job failed", f"Прогон отчёта {level} упал", error, level=level
        )
    if level == "daily" and backup_dir is not None:
        try:
            backup_alert = stale_backup_alert(backup_dir, now)
        except Exception as error:
            await alert_failure(deps, "backup check failed", "Проверка бэкапа упала", error)
            return
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
    scheduler: AsyncIOScheduler, deps: ReportDeps, snapshot_sources: SnapshotSources
) -> None:
    time_settings = deps.config.status_mapping.time
    timezone = ZoneInfo(time_settings.timezone)
    window_end = time_settings.daily_window_end
    retry_at = time_settings.snapshot_retry_at(date.today()).time()
    attempts: tuple[tuple[time, SnapshotTrigger], ...] = (
        (window_end, "scheduled"),
        (retry_at, "retry"),
    )
    for attempt_at, trigger in attempts:
        scheduler.add_job(
            snapshot_job,
            CronTrigger(hour=attempt_at.hour, minute=attempt_at.minute, timezone=timezone),
            args=[deps, snapshot_sources, trigger],
            id=f"snapshot_{attempt_at:%H%M}",
            misfire_grace_time=int(SNAPSHOT_MISFIRE_GRACE.total_seconds()),
        )
    check_at = (datetime.combine(date.min, window_end) + FINAL_SNAPSHOT_CHECK_DELAY).time()
    scheduler.add_job(
        final_snapshot_check_job,
        CronTrigger(hour=check_at.hour, minute=check_at.minute, timezone=timezone),
        args=[deps],
        id=f"snapshot_check_{check_at:%H%M}",
        misfire_grace_time=int(REPORT_MISFIRE_GRACE.total_seconds()),
    )
    missed_check_at = time_settings.missed_snapshot_check
    scheduler.add_job(
        missed_snapshot_job,
        CronTrigger(hour=missed_check_at.hour, minute=missed_check_at.minute, timezone=timezone),
        args=[deps],
        id=f"missed_snapshot_check_{missed_check_at:%H%M}",
        misfire_grace_time=int(REPORT_MISFIRE_GRACE.total_seconds()),
    )


def startup_announcement(app_version: str, dry_run: bool) -> str:
    return f"Бот запущен, версия {app_version}, DRY_RUN={int(dry_run)}."


def create_anthropic_client(app_settings: Settings) -> AsyncAnthropic | None:
    if app_settings.anthropic_api_key is None:
        return None
    return AsyncAnthropic(
        api_key=app_settings.anthropic_api_key.get_secret_value(),
        timeout=CHAT_API_TIMEOUT_SECONDS,
        max_retries=CHAT_API_MAX_RETRIES,
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
    # Отдельный httpx-клиент на ключ clients:read, пейсер у обоих общий (IP-лимит mefi один).
    mefi_clients_http_client = create_mefi_http_client(
        app_settings.mefi_base_url, app_settings.mefi_clients_api_key
    )
    snapshot_sources = SnapshotSources(
        leads_client=MefiClient(mefi_http_client),
        clients_client=MefiClient(mefi_clients_http_client),
        contact_hash_key=app_settings.contact_hash_key,
    )
    timezone = ZoneInfo(config.status_mapping.time.timezone)
    scheduler = AsyncIOScheduler(timezone=timezone)
    schedule_snapshot_jobs(scheduler, deps, snapshot_sources)
    reschedule = partial(schedule_report_job, scheduler, deps, app_settings.backup_dir)
    schedules_by_level = await stored_schedules(engine, app_settings.tenant_id)
    for level, schedule in schedules_by_level.items():
        if schedule.enabled:
            reschedule(level, schedule.cron)
    await notify_ops(deps.ops, startup_announcement(app_settings.app_version, app_settings.dry_run))
    schedule_alert = nonstandard_schedule_alert(config, schedules_by_level)
    if schedule_alert is not None:
        await notify_ops(deps.ops, schedule_alert)
    # До старта планировщика: иначе ежедневная джоба, сработавшая посреди позднего снапшота,
    # ушла бы с пометкой «нет данных», а догон её уже не переотправил бы.
    await take_late_snapshot(deps, snapshot_sources, datetime.now(timezone))
    # Граница с планировщиком: срабатывания до этого момента догоняет catch-up, после него
    # APScheduler (время следующего запуска он считает от start()).
    started_at = datetime.now(timezone)
    scheduler.start()
    logger.info(
        "scheduler started",
        extra={"jobs": [job.id for job in scheduler.get_jobs()], "dry_run": app_settings.dry_run},
    )
    catch_up_task = asyncio.create_task(catch_up_on_startup(deps, started_at, schedules_by_level))

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
        clock=partial(datetime.now, timezone),
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
    for task in [*polling_tasks, catch_up_task]:
        task.cancel()
    await asyncio.gather(*polling_tasks, catch_up_task, return_exceptions=True)
    await mefi_http_client.aclose()
    await mefi_clients_http_client.aclose()
    if anthropic_client is not None:
        await anthropic_client.close()
    await deps.report_bot.session.close()
    await deps.ops.bot.session.close()
