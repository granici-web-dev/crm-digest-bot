import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import partial
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pandas as pd
from aiogram import Bot
from markupsafe import Markup
from sqlalchemy import Row, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.config import AppConfig, ModuleRegistry, ReportModule, SourceCode
from digest.db.client_frame import load_client_frame
from digest.db.lead_frame import (
    SnapshotMissingError,
    load_lead_frame,
    previous_success_snapshot_date,
    success_snapshot_dates,
)
from digest.db.schema import (
    lead_snapshots,
    module_settings,
    report_runs,
    snapshot_runs,
)
from digest.delivery.ops import OpsChannel, notify_ops
from digest.delivery.telegram import (
    send_document_with_retry,
    send_message_with_retry,
    send_photo_with_retry,
    split_message,
)
from digest.metrics.chat_periods import first_snapshot_on_or_after
from digest.metrics.daily import PreviousSnapshot
from digest.metrics.frame import unknown_manager_ids
from digest.metrics.kpi import Period
from digest.metrics.touches import touch_snapshot_dates
from digest.metrics.weekly import DAYS_IN_WEEK, week_days
from digest.reports.context import ReportContext, ReportDocument, ReportPhoto
from digest.reports.lead_links import LeadLinks
from digest.reports.modules import ReportModuleFunction
from digest.reports.periods import ReportLevel, report_period
from digest.reports.render import render
from digest.snapshot import describe_error, late_snapshot_started_at

logger = logging.getLogger(__name__)

# Процесс, упавший посреди отправки, оставляет running навсегда. Дубль в группе после
# краха лучше, чем отчёт, которого нет до ручного UPDATE (shape 2026-09-25-delivery, вопрос 3).
STALE_RUNNING_AFTER = timedelta(minutes=30)
WEEK_OVER_WEEK_MODULE_ID = "w8"
IRRELEVANT_BY_CAMPAIGN_MODULE_ID = "w6"
TOUCHES_MODULE_ID = "w14"
CLIENTS_SOURCE: SourceCode = "I"

ReportRunOutcome = Literal["success", "partial", "failed", "already_sent", "in_progress"]


@dataclass(frozen=True)
class ReportReader:
    engine: AsyncEngine
    config: AppConfig
    tenant_id: str
    modules: Mapping[str, ReportModuleFunction]
    lead_links: LeadLinks


@dataclass(frozen=True)
class ReportDeps:
    engine: AsyncEngine
    config: AppConfig
    tenant_id: str
    report_bot: Bot
    ops: OpsChannel
    report_chat_id: int
    modules: Mapping[str, ReportModuleFunction]
    lead_links: LeadLinks

    @property
    def reader(self) -> ReportReader:
        return ReportReader(self.engine, self.config, self.tenant_id, self.modules, self.lead_links)


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
    blocks: tuple[ModuleBlock, ...] = ()
    # Сборка ничего не шлёт: алерты модулей отправляет run_report, аудит их только печатает.
    alerts: tuple[str, ...] = ()
    photos: tuple[ReportPhoto, ...] = ()
    documents: tuple[ReportDocument, ...] = ()


@dataclass(frozen=True)
class ReportInputs:
    lead_frame: pd.DataFrame
    context: ReportContext
    late_snapshot_at: datetime | None


def report_snapshot_date(period: Period, timezone: ZoneInfo) -> date:
    # Снапшот за последний день периода: weekly в понедельник читает воскресный снапшот.
    return (period.end - timedelta(microseconds=1)).astimezone(timezone).date()


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


async def module_enabled_overrides(reader: ReportReader) -> dict[str, bool]:
    async with reader.engine.connect() as connection:
        rows = await connection.execute(
            select(module_settings.c.module_id, module_settings.c.enabled).where(
                module_settings.c.tenant_id == reader.tenant_id
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


@dataclass(frozen=True)
class ModuleSelection:
    runnable: list[tuple[str, ReportModuleFunction]]
    # Включённые модули, которые не запустятся: причина уходит в служебный бот.
    alerts: list[str]


async def select_modules(reader: ReportReader, level: ReportLevel) -> ModuleSelection:
    overrides = await module_enabled_overrides(reader)
    runnable: list[tuple[str, ReportModuleFunction]] = []
    alerts: list[str] = []
    not_implemented: list[str] = []
    for module_id, module in modules_of_level(reader.config.modules, level).items():
        if not overrides.get(module_id, module.enabled):
            continue
        blockers = reader.config.module_blockers(module_id)
        if blockers.disconnected_sources:
            alerts.append(
                f"Модуль {module_id} включён, но источники "
                f"{list(blockers.disconnected_sources)} не подключены: пропущен."
            )
        elif blockers.missing_kpi_status is not None:
            alerts.append(
                f"Модуль {module_id} включён, но требует kpi.yaml status: "
                f"{blockers.missing_kpi_status}: пропущен."
            )
        elif module_id not in reader.modules:
            not_implemented.append(module_id)
        else:
            runnable.append((module_id, reader.modules[module_id]))
    if not_implemented:
        alerts.append(
            f"Модули {not_implemented} отчёта {level} включены, но не реализованы: "
            "в отчёт не попали."
        )
    return ModuleSelection(runnable, alerts)


UNKNOWN_KEY_PROBLEMS = ("unknown_raw_key", "unknown_custom_field")


def unknown_key_identity(mismatch: dict[str, Any]) -> tuple[str, int | None, str]:
    return mismatch["problem"], mismatch["field_id"], mismatch["expected_name"]


async def alert_won_mismatches(deps: ReportDeps, rows: Sequence[Row[Any]]) -> None:
    # Два разных сбоя: Clienți без converted_at это ошибка ввода, а converted_at вне Clienți
    # значит, что контракт был и лид ушёл в другой статус, возможно расторжение договора.
    without_conversion_ids: list[int] = []
    converted_ids_by_status: dict[str | None, list[int]] = {}
    for lead_id, status_name, converted_at in sorted(rows):
        if converted_at is None:
            without_conversion_ids.append(lead_id)
        else:
            converted_ids_by_status.setdefault(status_name, []).append(lead_id)
    if without_conversion_ids:
        await notify_ops(
            deps.ops,
            f"Новые лиды в Clienți без даты конверсии ({len(without_conversion_ids)}), "
            f"id: {without_conversion_ids}.",
        )
    for status_name, lead_ids in sorted(
        converted_ids_by_status.items(), key=lambda item: item[0] or ""
    ):
        status_label = f"«{status_name}»" if status_name is not None else "без статуса"
        await notify_ops(
            deps.ops,
            f"Новые лиды с датой конверсии ушли в статус {status_label} ({len(lead_ids)}), "
            f"id: {lead_ids}. Возможно расторжение договора.",
        )


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
        previous_findings = (
            (
                await connection.execute(
                    select(
                        snapshot_runs.c.custom_field_mismatches,
                        snapshot_runs.c.won_converted_mismatch_ids,
                    ).where(
                        snapshot_runs.c.tenant_id == deps.tenant_id,
                        snapshot_runs.c.snapshot_date == previous_date,
                        snapshot_runs.c.status == "success",
                    )
                )
            ).all()
            if previous_date is not None
            else []
        )
        previous_mismatches = [mismatches for mismatches, _ in previous_findings]
        # Как у UNMAPPED: расхождение алертится один раз, в день появления, иначе те же id
        # приходили бы каждый вечер.
        previously_won_mismatch_ids = {
            lead_id for _, lead_ids in previous_findings for lead_id in lead_ids or []
        }
        new_won_mismatch_ids = sorted(set(won_mismatch_ids or []) - previously_won_mismatch_ids)
        won_mismatch_rows = (
            await connection.execute(
                select(
                    lead_snapshots.c.lead_id,
                    lead_snapshots.c.status_name,
                    lead_snapshots.c.converted_at,
                ).where(
                    lead_snapshots.c.tenant_id == deps.tenant_id,
                    lead_snapshots.c.snapshot_date == snapshot_date,
                    lead_snapshots.c.lead_id.in_(new_won_mismatch_ids),
                )
            )
        ).all()
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
        unknown_key_identity(mismatch)
        for mismatches in previous_mismatches
        for mismatch in mismatches or []
        if mismatch["problem"] in UNKNOWN_KEY_PROBLEMS
    }
    new_unknown_keys = [
        mismatch
        for mismatch in custom_field_mismatches or []
        if mismatch["problem"] in UNKNOWN_KEY_PROBLEMS
        and unknown_key_identity(mismatch) not in previously_seen_keys
    ]
    for mismatch in new_unknown_keys:
        if mismatch["problem"] == "unknown_raw_key":
            await notify_ops(
                deps.ops,
                f"Незнакомый ключ лида «{mismatch['expected_name']}» в ответе mefi, "
                f"лидов: {mismatch['lead_count']}, в raw не записан. Проверьте, не контакт ли это, "
                "и добавьте в raw_known_keys config/status-mapping.yaml (свободный текст "
                "о клиенте или контакт также в raw_strip).",
            )
        else:
            await notify_ops(
                deps.ops,
                f"Незнакомое кастомное поле лида {mismatch['field_id']} "
                f"«{mismatch['expected_name']}» в ответе mefi, лидов: {mismatch['lead_count']}, "
                "в raw не записано. Добавьте field_id в raw_custom_fields "
                "config/status-mapping.yaml: в drop, если это свободный текст о клиенте, "
                "иначе в keep.",
            )
    await alert_won_mismatches(deps, won_mismatch_rows)
    unknown_ids = unknown_manager_ids(lead_frame, deps.config)
    if unknown_ids:
        await notify_ops(
            deps.ops,
            "Лиды на консультантах или созданные пользователями вне config/managers.yaml, "
            f"assigned_to.id или created_by.id: {sorted(unknown_ids)}.",
        )


async def snapshot_if_successful(
    reader: ReportReader, snapshot_date: date
) -> PreviousSnapshot | None:
    try:
        lead_frame = await load_lead_frame(
            reader.engine, reader.tenant_id, snapshot_date, reader.config
        )
    except SnapshotMissingError:
        return None
    return PreviousSnapshot(snapshot_date, lead_frame)


async def previous_week_snapshot(
    reader: ReportReader, week_ago: PreviousSnapshot | None, snapshot_date: date
) -> PreviousSnapshot | None:
    # Правило закрытого периода чата (period_snapshot_date): снапшот воскресенья прошлой недели,
    # нет его, первый успешный после, но строго раньше снапшота отчёта: свой снапшот отчёта дал
    # бы прошлой неделе дозревший IRELEVANT, и сравнение с текущей потеряло бы смысл
    # (docs/kpi-definitions.md, «Разбивка по источникам и кампаниям», w6). w6 подписывает подмену.
    if week_ago is not None:
        return week_ago
    week_end = snapshot_date - timedelta(days=DAYS_IN_WEEK)
    dates = await success_snapshot_dates(reader.engine, reader.tenant_id)
    substitute = first_snapshot_on_or_after(week_end, dates, until=snapshot_date)
    if substitute is None:
        return None
    return await snapshot_if_successful(reader, substitute)


async def touch_snapshot_chain(
    reader: ReportReader, snapshot_date: date, lead_frame: pd.DataFrame
) -> tuple[PreviousSnapshot, ...]:
    dates = touch_snapshot_dates(
        await success_snapshot_dates(reader.engine, reader.tenant_id),
        week_days(snapshot_date)[0],
        snapshot_date,
    )
    return tuple(
        [
            PreviousSnapshot(
                day, await load_lead_frame(reader.engine, reader.tenant_id, day, reader.config)
            )
            for day in dates
            if day != snapshot_date
        ]
        + [PreviousSnapshot(snapshot_date, lead_frame)]
    )


async def load_report_inputs(
    reader: ReportReader, level: ReportLevel, snapshot_date: date, module_ids: set[str]
) -> ReportInputs | None:
    try:
        lead_frame = await load_lead_frame(
            reader.engine, reader.tenant_id, snapshot_date, reader.config
        )
    except SnapshotMissingError:
        return None
    # Поздний снапшот сдвигает только ежедневный отчёт: его разница снапшотов захватила вечер.
    late_snapshot_at = (
        await late_snapshot_started_at(reader.engine, reader.tenant_id, snapshot_date)
        if level == "daily"
        else None
    )
    previous_date = await previous_success_snapshot_date(
        reader.engine, reader.tenant_id, snapshot_date
    )
    previous = (
        PreviousSnapshot(
            previous_date,
            await load_lead_frame(reader.engine, reader.tenant_id, previous_date, reader.config),
        )
        if previous_date is not None
        else None
    )
    # w8 сравнивает оферты только со снапшотом ровно за прошлое воскресенье: более старый покрыл
    # бы больше недели. Кадр нужен только w8, без него лишняя загрузка полного снапшота.
    week_ago = (
        await snapshot_if_successful(reader, snapshot_date - timedelta(days=DAYS_IN_WEEK))
        if WEEK_OVER_WEEK_MODULE_ID in module_ids
        else None
    )
    previous_week = (
        await previous_week_snapshot(reader, week_ago, snapshot_date)
        if IRRELEVANT_BY_CAMPAIGN_MODULE_ID in module_ids
        else None
    )
    # До восьми полных снапшотов: грузятся только для w14.
    touch_snapshots = (
        await touch_snapshot_chain(reader, snapshot_date, lead_frame)
        if TOUCHES_MODULE_ID in module_ids
        else ()
    )
    # Кадр клиентов нужен только модулям с источником I (clients:read), сейчас это d1.
    reads_clients = any(
        CLIENTS_SOURCE in reader.config.modules.all_modules[module_id].sources
        for module_id in module_ids
    )
    clients = (
        await load_client_frame(reader.engine, reader.tenant_id, snapshot_date, reader.config)
        if reads_clients
        else None
    )
    context = ReportContext(
        snapshot_date,
        previous,
        week_ago,
        previous_week,
        touch_snapshots,
        clients,
        reader.config,
        reader.tenant_id,
        reader.lead_links,
    )
    return ReportInputs(lead_frame, context, late_snapshot_at)


def render_report(
    config: AppConfig,
    level: ReportLevel,
    period: Period,
    snapshot_date: date,
    late: bool,
    modules: Sequence[tuple[str, ReportModuleFunction]],
    inputs: ReportInputs | None,
) -> BuiltReport:
    render_values = {
        "late": late,
        "level": level,
        "period_label": period_label(level, period),
        "snapshot_date": snapshot_date,
        "tenant_display_name": config.status_mapping.tenant_display_name,
    }
    if inputs is None:
        text = render(
            "report",
            blocks=[],
            snapshot_missing=True,
            late_snapshot_at=None,
            unavailable_sources=[],
            **render_values,
        )
        return BuiltReport(text, "partial", snapshot_date)

    blocks: list[ModuleBlock] = []
    alerts: list[str] = []
    photos: list[ReportPhoto] = []
    documents: list[ReportDocument] = []
    unavailable_sources: set[str] = set()
    for module_id, module_function in modules:
        try:
            result = module_function(inputs.lead_frame, inputs.context)
        except Exception as error:
            # str(error) может содержать строки raw (см. snapshot.describe_error).
            logger.error(
                "report module failed",
                extra={"module_id": module_id, "error": describe_error(error)},
            )
            alerts.append(f"Модуль {module_id} отчёта {level} упал: {describe_error(error)}.")
            blocks.append(ModuleBlock(module_id, None))
        else:
            alerts.extend(result.alerts)
            unavailable_sources.update(result.unavailable_sources)
            if result.photo is not None:
                photos.append(result.photo)
            if result.document is not None:
                documents.append(result.document)
            # Текст модуля уже отрендерен своим шаблоном с autoescape, второй раз не экранируем.
            blocks.append(ModuleBlock(module_id, Markup(result.text)))
    text = render(
        "report",
        blocks=blocks,
        snapshot_missing=False,
        late_snapshot_at=inputs.late_snapshot_at,
        daily_window_end=config.status_mapping.time.daily_window_end,
        unavailable_sources=sorted(unavailable_sources),
        **render_values,
    )
    status: Literal["success", "partial"] = (
        "partial" if any(block.text is None for block in blocks) else "success"
    )
    return BuiltReport(
        text,
        status,
        snapshot_date,
        tuple(blocks),
        tuple(alerts),
        tuple(photos),
        tuple(documents),
    )


async def build_and_alert(
    deps: ReportDeps, level: ReportLevel, period: Period, snapshot_date: date, late: bool
) -> BuiltReport | None:
    selection = await select_modules(deps.reader, level)
    for alert in selection.alerts:
        await notify_ops(deps.ops, alert)
    if not selection.runnable:
        return None
    module_ids = {module_id for module_id, _ in selection.runnable}
    inputs = await load_report_inputs(deps.reader, level, snapshot_date, module_ids)
    if inputs is None:
        await notify_ops(
            deps.ops,
            f"Снапшот {deps.tenant_id} за {snapshot_date} отсутствует или failed: "
            f"отчёт {level} уходит с пометкой «данные mefi недоступны».",
        )
    else:
        await alert_snapshot_findings(deps, snapshot_date, inputs.lead_frame)
    report = render_report(
        deps.config, level, period, snapshot_date, late, selection.runnable, inputs
    )
    for alert in report.alerts:
        await notify_ops(deps.ops, alert)
    return report


async def run_report(
    level: ReportLevel, now: datetime, deps: ReportDeps, *, late: bool
) -> ReportRunOutcome:
    chat_id = deps.report_chat_id
    time_settings = deps.config.status_mapping.time
    timezone = ZoneInfo(time_settings.timezone)
    period = report_period(level, now, time_settings)
    snapshot_date = report_snapshot_date(period, timezone)
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
        report = await build_and_alert(deps, level, period, snapshot_date, late)
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

    bot = deps.report_bot
    # Порядок в чате: текст, фото, документы.
    sends: list[Callable[[], Awaitable[int]]] = [
        *(partial(send_message_with_retry, bot, chat_id, part) for part in parts),
        *(
            partial(send_photo_with_retry, bot, chat_id, photo.filename, photo.content)
            for photo in report.photos
        ),
        *(
            partial(send_document_with_retry, bot, chat_id, document.filename, document.content)
            for document in report.documents
        ),
    ]
    message_ids: list[int] = []
    try:
        for send in sends:
            message_ids.append(await send())
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
            f"{describe_error(error)}. Отправлено частей: {len(message_ids)} из {len(sends)}.",
        )
        return "failed"
    await finish_report_run(deps, claim.run_id, report.status, report.snapshot_date)
    logger.info(
        "report sent",
        extra={
            **log_extra,
            "status": report.status,
            "parts": len(sends),
        },
    )
    return report.status
