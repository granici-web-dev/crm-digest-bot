import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import pandas as pd
from aiogram import Bot
from markupsafe import Markup
from pydantic import TypeAdapter
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.config import AppConfig, ModuleRegistry, ReportModule
from digest.db.lead_frame import (
    SnapshotMissingError,
    load_lead_frame,
    previous_success_snapshot_date,
)
from digest.db.schema import (
    lead_snapshots,
    module_settings,
    report_runs,
    settings,
    snapshot_runs,
)
from digest.delivery.ops import OpsChannel, notify_ops
from digest.delivery.telegram import send_message_with_retry, split_message
from digest.metrics.daily import PreviousSnapshot
from digest.metrics.frame import unknown_manager_ids
from digest.metrics.kpi import Period
from digest.reports.context import ReportContext
from digest.reports.modules import ReportModuleFunction
from digest.reports.periods import ReportLevel, report_period
from digest.reports.render import ReportLanguage, render
from digest.snapshot import describe_error

logger = logging.getLogger(__name__)

# Процесс, упавший посреди отправки, оставляет running навсегда. Дубль в группе после
# краха лучше, чем отчёт, которого нет до ручного UPDATE (shape 2026-09-25-delivery, вопрос 3).
STALE_RUNNING_AFTER = timedelta(minutes=30)

ReportRunOutcome = Literal["success", "partial", "failed", "already_sent", "in_progress"]


@dataclass(frozen=True)
class ReportDeps:
    engine: AsyncEngine
    config: AppConfig
    tenant_id: str
    report_bot: Bot
    ops: OpsChannel
    report_chat_id: int
    modules: Mapping[str, ReportModuleFunction]


@dataclass(frozen=True)
class ReportRunClaim:
    run_id: int
    interrupted: bool
    previously_sent_message_ids: list[int]


@dataclass(frozen=True)
class ModuleBlock:
    module_id: str
    text: Markup | None


@dataclass(frozen=True)
class BuiltReport:
    text: str
    status: Literal["success", "partial"]
    snapshot_date: date


def period_label(level: ReportLevel, period: Period) -> str:
    if level == "daily":
        return f"{period.start:%d.%m.%Y %H:%M} → {period.end:%d.%m.%Y %H:%M}"
    return f"{period.start:%d.%m.%Y} – {period.end - timedelta(days=1):%d.%m.%Y}"


async def claim_report_run(
    deps: ReportDeps, level: ReportLevel, period: Period, chat_id: int
) -> ReportRunClaim | ReportRunOutcome:
    key = (
        (report_runs.c.tenant_id == deps.tenant_id)
        & (report_runs.c.report_level == level)
        & (report_runs.c.period_start == period.start)
        & (report_runs.c.chat_id == chat_id)
    )
    async with deps.engine.begin() as connection:
        inserted_id = await connection.scalar(
            pg_insert(report_runs)
            .values(
                tenant_id=deps.tenant_id,
                report_level=level,
                period_start=period.start,
                period_end=period.end,
                chat_id=chat_id,
                status="running",
            )
            .on_conflict_do_nothing()
            .returning(report_runs.c.id)
        )
        if inserted_id is not None:
            return ReportRunClaim(inserted_id, interrupted=False, previously_sent_message_ids=[])
        existing = (
            await connection.execute(
                select(
                    report_runs.c.id,
                    report_runs.c.status,
                    report_runs.c.started_at < func.now() - STALE_RUNNING_AFTER,
                    report_runs.c.message_ids,
                )
                .where(key)
                .with_for_update()
            )
        ).one()
        run_id, status, is_stale, previously_sent_message_ids = existing
        if status in ("success", "partial"):
            return "already_sent"
        if status == "running" and not is_stale:
            return "in_progress"
        await connection.execute(
            update(report_runs)
            .where(report_runs.c.id == run_id)
            # message_ids не затираем: по ним видно, какие части уже лежат в группе.
            .values(status="running", started_at=func.now(), finished_at=None, error=None)
        )
    return ReportRunClaim(
        run_id,
        interrupted=status == "running",
        previously_sent_message_ids=list(previously_sent_message_ids or []),
    )


async def finish_report_run(
    deps: ReportDeps,
    run_id: int,
    status: Literal["success", "partial", "failed"],
    snapshot_date: date | None = None,
    error: str | None = None,
) -> None:
    async with deps.engine.begin() as connection:
        await connection.execute(
            update(report_runs)
            .where(report_runs.c.id == run_id)
            .values(status=status, snapshot_date=snapshot_date, error=error, finished_at=func.now())
        )


async def record_message_ids(deps: ReportDeps, run_id: int, message_ids: list[int]) -> None:
    async with deps.engine.begin() as connection:
        await connection.execute(
            update(report_runs).where(report_runs.c.id == run_id).values(message_ids=message_ids)
        )


async def report_language(deps: ReportDeps) -> ReportLanguage:
    async with deps.engine.connect() as connection:
        value = await connection.scalar(
            select(settings.c.value).where(
                settings.c.tenant_id == deps.tenant_id, settings.c.key == "report_language"
            )
        )
    return TypeAdapter(ReportLanguage).validate_python(value)


async def module_enabled_overrides(deps: ReportDeps) -> dict[str, bool]:
    async with deps.engine.connect() as connection:
        rows = await connection.execute(
            select(module_settings.c.module_id, module_settings.c.enabled).where(
                module_settings.c.tenant_id == deps.tenant_id
            )
        )
        return {module_id: enabled for module_id, enabled in rows}


def modules_of_level(registry: ModuleRegistry, level: ReportLevel) -> dict[str, ReportModule]:
    return {
        "daily": registry.daily,
        "weekly": registry.weekly,
        "monthly": registry.monthly,
        "yearly": registry.yearly,
    }[level]


async def runnable_modules(
    deps: ReportDeps, level: ReportLevel
) -> list[tuple[str, ReportModuleFunction]]:
    registry = deps.config.modules
    overrides = await module_enabled_overrides(deps)
    runnable: list[tuple[str, ReportModuleFunction]] = []
    not_implemented: list[str] = []
    for module_id, module in modules_of_level(registry, level).items():
        if not overrides.get(module_id, module.enabled):
            continue
        disconnected = [code for code in module.sources if not registry.sources[code].connected]
        if disconnected:
            await notify_ops(
                deps.ops,
                f"Модуль {module_id} включён, но источники {disconnected} не подключены: пропущен.",
            )
        elif module_id not in deps.modules:
            not_implemented.append(module_id)
        else:
            runnable.append((module_id, deps.modules[module_id]))
    if not_implemented:
        await notify_ops(
            deps.ops,
            f"Модули {not_implemented} отчёта {level} включены, но не реализованы: "
            "в отчёт не попали.",
        )
    return runnable


async def alert_snapshot_findings(
    deps: ReportDeps, snapshot_date: date, lead_frame: pd.DataFrame
) -> None:
    previous_date = await previous_success_snapshot_date(deps.engine, deps.tenant_id, snapshot_date)
    async with deps.engine.connect() as connection:
        findings = (
            await connection.execute(
                select(
                    snapshot_runs.c.new_unmapped_lead_ids,
                    snapshot_runs.c.won_converted_mismatch_ids,
                    snapshot_runs.c.custom_field_mismatches,
                ).where(
                    snapshot_runs.c.tenant_id == deps.tenant_id,
                    snapshot_runs.c.snapshot_date == snapshot_date,
                    snapshot_runs.c.status == "success",
                )
            )
        ).one()
        new_unmapped_ids, won_mismatch_ids, custom_field_mismatches = findings
        # Статус берётся из снапшота, а не из кадра: в кадре нет лидов тестового аккаунта.
        unmapped_status_rows = (
            await connection.execute(
                select(lead_snapshots.c.lead_id, lead_snapshots.c.status_name).where(
                    lead_snapshots.c.tenant_id == deps.tenant_id,
                    lead_snapshots.c.snapshot_date == snapshot_date,
                    lead_snapshots.c.lead_id.in_(new_unmapped_ids or []),
                )
            )
        ).all()
        previous_mismatches = (
            (
                await connection.execute(
                    select(snapshot_runs.c.custom_field_mismatches).where(
                        snapshot_runs.c.tenant_id == deps.tenant_id,
                        snapshot_runs.c.snapshot_date == previous_date,
                        snapshot_runs.c.status == "success",
                    )
                )
            )
            .scalars()
            .all()
            if previous_date is not None
            else []
        )
    ids_by_unknown_status: dict[str, list[int]] = {}
    without_status_ids: list[int] = []
    for lead_id, status_name in sorted(unmapped_status_rows):
        if status_name is None:
            without_status_ids.append(lead_id)
        else:
            ids_by_unknown_status.setdefault(status_name, []).append(lead_id)
    if without_status_ids:
        await notify_ops(
            deps.ops,
            f"Новые лиды без статуса (Necompletat), UNMAPPED ({len(without_status_ids)}), "
            f"id: {without_status_ids}.",
        )
    for status_name, lead_ids in sorted(ids_by_unknown_status.items()):
        await notify_ops(
            deps.ops,
            f"Новые лиды с неизвестным статусом «{status_name}», UNMAPPED ({len(lead_ids)}), "
            f"id: {lead_ids}. Добавьте статус в config/status-mapping.yaml.",
        )
    # Как у UNMAPPED: алерт только при первом появлении ключа, иначе он повторялся бы
    # каждый день до правки конфига.
    previously_seen_keys = {
        mismatch["expected_name"]
        for mismatches in previous_mismatches
        for mismatch in mismatches or []
        if mismatch["problem"] == "unknown_raw_key"
    }
    unknown_raw_keys = [
        mismatch
        for mismatch in custom_field_mismatches or []
        if mismatch["problem"] == "unknown_raw_key"
        and mismatch["expected_name"] not in previously_seen_keys
    ]
    for mismatch in unknown_raw_keys:
        await notify_ops(
            deps.ops,
            f"Незнакомый ключ лида «{mismatch['expected_name']}» в ответе mefi, "
            f"лидов: {mismatch['lead_count']}, сохранён в raw. Проверьте, не контакт ли это, "
            "и добавьте в raw_known_keys или raw_strip config/status-mapping.yaml.",
        )
    if won_mismatch_ids:
        await notify_ops(
            deps.ops,
            f"Clienți и converted_at расходятся ({len(won_mismatch_ids)}), "
            f"id: {sorted(won_mismatch_ids)}.",
        )
    unknown_ids = unknown_manager_ids(lead_frame, deps.config)
    if unknown_ids:
        await notify_ops(
            deps.ops,
            "Лиды на консультантах или созданные пользователями вне config/managers.yaml, "
            f"assigned_to.id или created_by.id: {sorted(unknown_ids)}.",
        )


async def build_report(
    deps: ReportDeps, level: ReportLevel, period: Period, snapshot_date: date
) -> BuiltReport | None:
    modules = await runnable_modules(deps, level)
    if not modules:
        return None
    language = await report_language(deps)
    render_values = {
        "level": level,
        "period_label": period_label(level, period),
        "snapshot_date": snapshot_date,
    }
    try:
        lead_frame = await load_lead_frame(deps.engine, deps.tenant_id, snapshot_date, deps.config)
    except SnapshotMissingError:
        await notify_ops(
            deps.ops,
            f"Снапшот {deps.tenant_id} за {snapshot_date} отсутствует или failed: "
            f"отчёт {level} уходит с пометкой «данные mefi недоступны».",
        )
        text = render(
            "report",
            language,
            blocks=[],
            snapshot_missing=True,
            unavailable_sources=[],
            **render_values,
        )
        return BuiltReport(text, "partial", snapshot_date)

    await alert_snapshot_findings(deps, snapshot_date, lead_frame)
    previous_date = await previous_success_snapshot_date(deps.engine, deps.tenant_id, snapshot_date)
    previous = (
        PreviousSnapshot(
            previous_date,
            await load_lead_frame(deps.engine, deps.tenant_id, previous_date, deps.config),
        )
        if previous_date is not None
        else None
    )
    context = ReportContext(snapshot_date, previous, deps.config, language)
    blocks: list[ModuleBlock] = []
    unavailable_sources: set[str] = set()
    for module_id, module_function in modules:
        try:
            result = module_function(lead_frame, context)
        except Exception as error:
            # str(error) может содержать строки raw (см. snapshot.describe_error).
            logger.error(
                "report module failed",
                extra={"module_id": module_id, "error": describe_error(error)},
            )
            await notify_ops(
                deps.ops, f"Модуль {module_id} отчёта {level} упал: {describe_error(error)}."
            )
            blocks.append(ModuleBlock(module_id, None))
        else:
            for alert in result.alerts:
                await notify_ops(deps.ops, alert)
            unavailable_sources.update(result.unavailable_sources)
            # Текст модуля уже отрендерен своим шаблоном с autoescape, второй раз не экранируем.
            blocks.append(ModuleBlock(module_id, Markup(result.text)))
    text = render(
        "report",
        language,
        blocks=blocks,
        snapshot_missing=False,
        unavailable_sources=sorted(unavailable_sources),
        **render_values,
    )
    status: Literal["success", "partial"] = (
        "partial" if any(block.text is None for block in blocks) else "success"
    )
    return BuiltReport(text, status, snapshot_date)


async def run_report(level: ReportLevel, now: datetime, deps: ReportDeps) -> ReportRunOutcome:
    chat_id = deps.report_chat_id
    time_settings = deps.config.status_mapping.time
    timezone = ZoneInfo(time_settings.timezone)
    period = report_period(level, now, time_settings)
    # Снапшот за последний день периода: weekly в понедельник читает воскресный снапшот.
    snapshot_date = (period.end - timedelta(microseconds=1)).astimezone(timezone).date()
    label = period_label(level, period)
    log_extra = {"level": level, "period_start": period.start.isoformat(), "chat_id": chat_id}

    claim = await claim_report_run(deps, level, period, chat_id)
    if not isinstance(claim, ReportRunClaim):
        logger.info("report run skipped", extra={**log_extra, "reason": claim})
        if claim == "in_progress":
            await notify_ops(
                deps.ops,
                f"Отчёт {level} за {label} уже отправляется другим прогоном (моложе "
                f"{STALE_RUNNING_AFTER.seconds // 60} минут): этот запуск ничего не шлёт.",
            )
        return claim
    previously_sent = len(claim.previously_sent_message_ids)
    if claim.interrupted:
        await notify_ops(
            deps.ops,
            f"Прогон отчёта {level} за {label} прерван, отчёт отправлен заново. "
            f"Ранее отправлено частей: {previously_sent}.",
        )
    elif previously_sent:
        await notify_ops(
            deps.ops,
            f"Отчёт {level} за {label} отправляется повторно после сбоя. "
            f"Ранее отправлено частей: {previously_sent}, в чате будет дубль.",
        )

    try:
        report = await build_report(deps, level, period, snapshot_date)
        parts = split_message(report.text) if report is not None else []
    except Exception as error:
        logger.error("report build failed", extra={**log_extra, "error": describe_error(error)})
        await finish_report_run(deps, claim.run_id, "failed", error=describe_error(error))
        await notify_ops(deps.ops, f"Отчёт {level} за {label} не собран: {describe_error(error)}.")
        return "failed"
    if report is None:
        await finish_report_run(deps, claim.run_id, "failed", error="no modules")
        await notify_ops(
            deps.ops, f"Отчёт {level} за {label} не отправлен: нет ни одного реализованного модуля."
        )
        return "failed"

    message_ids: list[int] = []
    try:
        for part in parts:
            message_ids.append(await send_message_with_retry(deps.report_bot, chat_id, part))
            await record_message_ids(
                deps, claim.run_id, [*claim.previously_sent_message_ids, *message_ids]
            )
    except Exception as error:
        await finish_report_run(
            deps, claim.run_id, "failed", report.snapshot_date, describe_error(error)
        )
        await notify_ops(
            deps.ops,
            f"Отправка отчёта {level} за {label} в чат {chat_id} не удалась: "
            f"{describe_error(error)}. Отправлено частей: {len(message_ids)} из {len(parts)}.",
        )
        return "failed"
    await finish_report_run(deps, claim.run_id, report.status, report.snapshot_date)
    logger.info("report sent", extra={**log_extra, "status": report.status, "parts": len(parts)})
    return report.status
