from collections.abc import Collection, Mapping
from datetime import datetime, time
from zoneinfo import ZoneInfo

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from apscheduler.triggers.cron import CronTrigger

from digest.config import AppConfig, SettingsLevel
from digest.reports.runner import modules_of_level

LEVEL_TITLES: dict[SettingsLevel, str] = {
    "daily": "Zilnic",
    "weekly": "Săptămânal",
    "monthly": "Lunar",
}
ROOT_TEXT = "Setări rapoarte. Alegeți raportul:"
BACK_BUTTON = "⬅ Înapoi"
SEND_TIME_BUTTON = "🕒 Ora"
UNIMPLEMENTED_REASON = "în lucru"
NONSTANDARD_SCHEDULE_TEXT = "program nestandard"
MISSED_REPORT_SENT_TEXT = "raportul de azi a fost trimis acum"


class RootMenu(CallbackData, prefix="mr"):
    pass


class LevelMenu(CallbackData, prefix="ml"):
    level: SettingsLevel


class ModuleSwitch(CallbackData, prefix="mt"):
    module_id: str
    enabled: bool


class SendTimeMenu(CallbackData, prefix="tm"):
    level: SettingsLevel


class SendTimeChoice(CallbackData, prefix="ts"):
    level: SettingsLevel
    hhmm: str


def module_unavailable_reason(
    config: AppConfig, module_id: str, implemented_module_ids: Collection[str]
) -> str | None:
    blockers = config.module_blockers(module_id)
    if blockers.disconnected_sources:
        codes = ", ".join(blockers.disconnected_sources)
        noun = "sursa" if len(blockers.disconnected_sources) == 1 else "sursele"
        return f"nu e disponibil: {noun} {codes}"
    if blockers.missing_kpi_status is not None:
        return "nu e disponibil: KPI necalibrat"
    if module_id not in implemented_module_ids:
        return UNIMPLEMENTED_REASON
    return None


def standard_send_time(config: AppConfig, level: SettingsLevel, cron: str) -> time | None:
    # Cron, поправленный в schedules вручную (*/5, 0,30, 18:15), меню не отображает и не меняет.
    minute, hour = cron.split()[:2]
    for option in config.modules.send_times[level]:
        if (minute, hour) == (str(option.minute), str(option.hour)):
            return option
    return None


def cron_with_send_time(cron: str, send_time: time) -> str:
    return " ".join([str(send_time.minute), str(send_time.hour), *cron.split()[2:]])


def report_missed_today(old_cron: str, new_cron: str, now: datetime, timezone: ZoneInfo) -> bool:
    # Перенос на время, уже прошедшее сегодня, до старого срабатывания: без досылки отчёт за
    # сегодня не ушёл бы вовсе, потому что планировщик считает следующий запуск от текущего момента.
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    new_fire: datetime | None = CronTrigger.from_crontab(
        new_cron, timezone=timezone
    ).get_next_fire_time(None, day_start)
    old_fire: datetime | None = CronTrigger.from_crontab(
        old_cron, timezone=timezone
    ).get_next_fire_time(None, day_start)
    if new_fire is None or old_fire is None:
        return False
    return new_fire.date() == now.date() and new_fire <= now < old_fire


def send_time_option(config: AppConfig, level: SettingsLevel, hhmm: str) -> time | None:
    for option in config.modules.send_times[level]:
        if f"{option:%H%M}" == hhmm:
            return option
    return None


def format_send_time(send_time: time) -> str:
    return f"{send_time:%H:%M}"


def root_menu() -> tuple[str, InlineKeyboardMarkup]:
    rows = [
        [InlineKeyboardButton(text=title, callback_data=LevelMenu(level=level).pack())]
        for level, title in LEVEL_TITLES.items()
    ]
    return ROOT_TEXT, InlineKeyboardMarkup(inline_keyboard=rows)


def level_menu(
    config: AppConfig,
    level: SettingsLevel,
    enabled_by_module: Mapping[str, bool],
    implemented_module_ids: Collection[str],
    send_time: time | None,
) -> tuple[str, InlineKeyboardMarkup]:
    rows: list[list[InlineKeyboardButton]] = []
    for module_id, module in modules_of_level(config.modules, level).items():
        reason = module_unavailable_reason(config, module_id, implemented_module_ids)
        enabled = enabled_by_module[module_id]
        if reason is None:
            text = f"{'✅' if enabled else '⬜'} {module_id} · {module.label}"
        elif enabled:
            # Включён до того, как источник отключили: выключить можно, включить обратно нельзя.
            text = f"✅ {module_id} · {module.label} · {reason}"
        else:
            text = f"▫️ {module_id} · {module.label} · {reason}"
        switch = ModuleSwitch(module_id=module_id, enabled=not enabled)
        rows.append([InlineKeyboardButton(text=text, callback_data=switch.pack())])
    back_button = InlineKeyboardButton(text=BACK_BUTTON, callback_data=RootMenu().pack())
    if send_time is None:
        rows.append([back_button])
        schedule_text = NONSTANDARD_SCHEDULE_TEXT
    else:
        send_time_button = InlineKeyboardButton(
            text=SEND_TIME_BUTTON, callback_data=SendTimeMenu(level=level).pack()
        )
        rows.append([send_time_button, back_button])
        schedule_text = f"ora {format_send_time(send_time)}"
    text = f"{LEVEL_TITLES[level]} · {schedule_text}. Apăsați pe modul:"
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def send_time_menu(
    config: AppConfig, level: SettingsLevel, current: time
) -> tuple[str, InlineKeyboardMarkup]:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'● ' if option == current else ''}{format_send_time(option)}",
                callback_data=SendTimeChoice(level=level, hhmm=f"{option:%H%M}").pack(),
            )
        ]
        for option in config.modules.send_times[level]
    ]
    rows.append(
        [InlineKeyboardButton(text=BACK_BUTTON, callback_data=LevelMenu(level=level).pack())]
    )
    text = f"{LEVEL_TITLES[level]} · ora de trimitere, acum {format_send_time(current)}:"
    return text, InlineKeyboardMarkup(inline_keyboard=rows)
