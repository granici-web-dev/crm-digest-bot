import logging
from collections.abc import Callable, Collection
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Dispatcher, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, ErrorEvent, InlineKeyboardMarkup, Message
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from digest.bot.settings_menu import (
    MISSED_REPORT_SENT_TEXT,
    NONSTANDARD_SCHEDULE_TEXT,
    LevelMenu,
    ModuleSwitch,
    RootMenu,
    SendTimeChoice,
    SendTimeMenu,
    cron_with_send_time,
    format_send_time,
    level_menu,
    module_unavailable_reason,
    report_missed_today,
    root_menu,
    send_time_menu,
    send_time_option,
    standard_send_time,
)
from digest.config import SETTINGS_LEVELS, SettingsLevel
from digest.db.schema import module_settings, schedules
from digest.delivery.ops import notify_ops
from digest.reports.runner import (
    ReportDeps,
    module_enabled_overrides,
    modules_of_level,
    run_report,
)
from digest.snapshot import describe_error

logger = logging.getLogger(__name__)

RescheduleReport = Callable[[SettingsLevel, str], None]
Clock = Callable[[], datetime]
Menu = tuple[str, InlineKeyboardMarkup]

STALE_MENU_TEXT = "Meniul e vechi, trimiteți /settings."
HANDLER_ERROR_TEXT = "Nu s-a putut aplica. Încercați din nou mai târziu."


def settings_level_of_module(deps: ReportDeps, module_id: str) -> SettingsLevel | None:
    for level in SETTINGS_LEVELS:
        if module_id in modules_of_level(deps.config.modules, level):
            return level
    return None


async def level_cron(deps: ReportDeps, level: SettingsLevel) -> str:
    async with deps.engine.connect() as connection:
        cron: str = (
            await connection.execute(
                select(schedules.c.cron).where(
                    schedules.c.tenant_id == deps.tenant_id, schedules.c.report_level == level
                )
            )
        ).scalar_one()
    return cron


async def load_level_menu(deps: ReportDeps, level: SettingsLevel) -> Menu:
    overrides = await module_enabled_overrides(deps)
    enabled_by_module = {
        module_id: overrides.get(module_id, module.enabled)
        for module_id, module in modules_of_level(deps.config.modules, level).items()
    }
    send_time = standard_send_time(deps.config, level, await level_cron(deps, level))
    return level_menu(deps.config, level, enabled_by_module, deps.modules, send_time)


async def show_menu(callback: CallbackQuery, menu: Menu) -> None:
    # Сообщение старше 48 часов Telegram отдаёт как InaccessibleMessage: править его нельзя.
    if not isinstance(callback.message, Message):
        await callback.answer(STALE_MENU_TEXT, show_alert=True)
        return
    text, keyboard = menu
    await callback.message.edit_text(text, reply_markup=keyboard)
    await callback.answer()


async def show_root(message: Message) -> None:
    text, keyboard = root_menu()
    await message.answer(text, reply_markup=keyboard)


async def open_root(callback: CallbackQuery) -> None:
    await show_menu(callback, root_menu())


async def open_level(callback: CallbackQuery, callback_data: LevelMenu, deps: ReportDeps) -> None:
    await show_menu(callback, await load_level_menu(deps, callback_data.level))


async def switch_module(
    callback: CallbackQuery, callback_data: ModuleSwitch, deps: ReportDeps
) -> None:
    module_id = callback_data.module_id
    level = settings_level_of_module(deps, module_id)
    if level is None:
        await callback.answer()
        return
    # Выключать можно всегда: модуль с отключённым источником иначе слал бы алерт каждый прогон.
    reason = module_unavailable_reason(deps.config, module_id, deps.modules)
    if callback_data.enabled and reason is not None:
        await callback.answer(reason, show_alert=True)
        return
    module = deps.config.modules.all_modules[module_id]
    was_enabled = (await module_enabled_overrides(deps)).get(module_id, module.enabled)
    if was_enabled == callback_data.enabled:
        await callback.answer()
        return
    async with deps.engine.begin() as connection:
        values = {"enabled": callback_data.enabled, "updated_by": callback.from_user.id}
        await connection.execute(
            pg_insert(module_settings)
            .values(tenant_id=deps.tenant_id, module_id=module_id, **values)
            .on_conflict_do_update(
                index_elements=[module_settings.c.tenant_id, module_settings.c.module_id],
                set_={**values, "updated_at": func.now()},
            )
        )
    admin = callback.from_user
    await notify_ops(
        deps.ops,
        f"Настройки: {admin.full_name} ({admin.id}): {module_id} {module.name} "
        f"{'включён' if was_enabled else 'выключен'} → "
        f"{'включён' if callback_data.enabled else 'выключен'}",
    )
    await show_menu(callback, await load_level_menu(deps, level))


async def open_send_time(
    callback: CallbackQuery, callback_data: SendTimeMenu, deps: ReportDeps
) -> None:
    level = callback_data.level
    current = standard_send_time(deps.config, level, await level_cron(deps, level))
    if current is None:
        await callback.answer(NONSTANDARD_SCHEDULE_TEXT, show_alert=True)
        return
    await show_menu(callback, send_time_menu(deps.config, level, current))


async def choose_send_time(
    callback: CallbackQuery,
    callback_data: SendTimeChoice,
    deps: ReportDeps,
    reschedule: RescheduleReport,
    clock: Clock,
) -> None:
    level = callback_data.level
    new_time = send_time_option(deps.config, level, callback_data.hhmm)
    if new_time is None:
        await callback.answer()
        return
    async with deps.engine.begin() as connection:
        # Два админа почти одновременно: без блокировки джоба могла бы остаться со временем
        # проигравшей транзакции, а в schedules записано другое.
        await connection.execute(
            select(func.pg_advisory_xact_lock(func.hashtext(f"schedules:{deps.tenant_id}:{level}")))
        )
        old_cron, schedule_enabled = (
            await connection.execute(
                select(schedules.c.cron, schedules.c.enabled).where(
                    schedules.c.tenant_id == deps.tenant_id, schedules.c.report_level == level
                )
            )
        ).one()
        old_time = standard_send_time(deps.config, level, old_cron)
        new_cron = cron_with_send_time(old_cron, new_time)
        changed = old_time is not None and old_time != new_time
        if changed:
            await connection.execute(
                update(schedules)
                .where(schedules.c.tenant_id == deps.tenant_id, schedules.c.report_level == level)
                .values(cron=new_cron, updated_by=callback.from_user.id, updated_at=func.now())
            )
            if schedule_enabled:
                # До коммита: сбой перепланирования откатывает запись, schedules и джоба не
                # расходятся, а ошибку в ops отправляет обработчик ошибок диспетчера.
                reschedule(level, new_cron)
    if old_time is None:
        await callback.answer(NONSTANDARD_SCHEDULE_TEXT, show_alert=True)
        return
    if not changed:
        await callback.answer()
        return
    admin = callback.from_user
    await notify_ops(
        deps.ops,
        f"Настройки: {admin.full_name} ({admin.id}): время {level} "
        f"{format_send_time(old_time)} → {format_send_time(new_time)}",
    )
    await show_menu(callback, send_time_menu(deps.config, level, new_time))
    now = clock()
    timezone = ZoneInfo(deps.config.status_mapping.time.timezone)
    if schedule_enabled and report_missed_today(old_cron, new_cron, now, timezone):
        # Повторную отправку за тот же период отсекает report_runs, дубля в группе не будет.
        outcome = await run_report(level, now, deps)
        if outcome in ("success", "partial"):
            await deps.report_bot.send_message(callback.from_user.id, MISSED_REPORT_SENT_TEXT)
            await notify_ops(deps.ops, f"Настройки: {level}: {MISSED_REPORT_SENT_TEXT}")


def settings_router(admin_ids: Collection[int]) -> Router:
    # Чужим и в группах бот молчит: без подходящего хендлера апдейт отбрасывается без ответа.
    router = Router(name="settings")
    router.message.filter(F.chat.type == ChatType.PRIVATE, F.from_user.id.in_(admin_ids))
    router.callback_query.filter(
        F.message.chat.type == ChatType.PRIVATE, F.from_user.id.in_(admin_ids)
    )
    router.message.register(show_root, Command("start", "settings"))
    router.callback_query.register(open_root, RootMenu.filter())
    router.callback_query.register(open_level, LevelMenu.filter())
    router.callback_query.register(switch_module, ModuleSwitch.filter())
    router.callback_query.register(open_send_time, SendTimeMenu.filter())
    router.callback_query.register(choose_send_time, SendTimeChoice.filter())
    return router


async def report_handler_error(event: ErrorEvent, deps: ReportDeps) -> None:
    logger.error("settings handler failed", extra={"error": describe_error(event.exception)})
    await notify_ops(deps.ops, f"/settings: хендлер упал: {describe_error(event.exception)}.")
    callback = event.update.callback_query
    if callback is not None:
        await callback.answer(HANDLER_ERROR_TEXT, show_alert=True)


def settings_dispatcher(
    deps: ReportDeps, reschedule: RescheduleReport, admin_ids: Collection[int], clock: Clock
) -> Dispatcher:
    dispatcher = Dispatcher(deps=deps, reschedule=reschedule, clock=clock)
    dispatcher.errors.register(report_handler_error)
    dispatcher.include_router(settings_router(admin_ids))
    return dispatcher
