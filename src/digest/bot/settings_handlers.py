from collections.abc import Callable, Collection

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message, User
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from digest.bot.settings_menu import (
    LevelMenu,
    ModuleSwitch,
    RootMenu,
    SendTimeChoice,
    SendTimeMenu,
    cron_send_time,
    cron_with_send_time,
    format_send_time,
    level_menu,
    module_unavailable_reason,
    root_menu,
    send_time_menu,
    send_time_option,
)
from digest.config import SETTINGS_LEVELS, SettingsLevel
from digest.db.schema import module_settings, schedules
from digest.delivery.ops import notify_ops
from digest.reports.runner import ReportDeps, module_enabled_overrides, modules_of_level

RescheduleReport = Callable[[SettingsLevel, str], None]
Menu = tuple[str, InlineKeyboardMarkup]

STALE_MENU_TEXT = "Meniul e vechi, trimiteți /settings."


def enabled_state(enabled: bool) -> str:
    return "включён" if enabled else "выключен"


def admin_label(user: User) -> str:
    return f"{user.full_name} ({user.id})"


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
    send_time = cron_send_time(await level_cron(deps, level))
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
    reason = module_unavailable_reason(deps.config, module_id, deps.modules)
    if reason is not None:
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
    await notify_ops(
        deps.ops,
        f"Настройки: {admin_label(callback.from_user)}: {module_id} {module.name} "
        f"{enabled_state(was_enabled)} → {enabled_state(callback_data.enabled)}",
    )
    await show_menu(callback, await load_level_menu(deps, level))


async def open_send_time(
    callback: CallbackQuery, callback_data: SendTimeMenu, deps: ReportDeps
) -> None:
    current = cron_send_time(await level_cron(deps, callback_data.level))
    await show_menu(callback, send_time_menu(deps.config, callback_data.level, current))


async def choose_send_time(
    callback: CallbackQuery,
    callback_data: SendTimeChoice,
    deps: ReportDeps,
    reschedule: RescheduleReport,
) -> None:
    level = callback_data.level
    new_time = send_time_option(deps.config, level, callback_data.hhmm)
    if new_time is None:
        await callback.answer()
        return
    cron = await level_cron(deps, level)
    old_time = cron_send_time(cron)
    if old_time == new_time:
        await callback.answer()
        return
    new_cron = cron_with_send_time(cron, new_time)
    async with deps.engine.begin() as connection:
        schedule_enabled: bool = (
            await connection.execute(
                update(schedules)
                .where(schedules.c.tenant_id == deps.tenant_id, schedules.c.report_level == level)
                .values(cron=new_cron, updated_by=callback.from_user.id, updated_at=func.now())
                .returning(schedules.c.enabled)
            )
        ).scalar_one()
    if schedule_enabled:
        reschedule(level, new_cron)
    await notify_ops(
        deps.ops,
        f"Настройки: {admin_label(callback.from_user)}: время {level} "
        f"{format_send_time(old_time)} → {format_send_time(new_time)}",
    )
    await show_menu(callback, send_time_menu(deps.config, level, new_time))


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
