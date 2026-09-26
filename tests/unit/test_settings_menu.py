from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest
from aiogram.types import InlineKeyboardMarkup

from digest.bot.settings_menu import (
    LevelMenu,
    ModuleSwitch,
    SendTimeChoice,
    cron_with_send_time,
    level_menu,
    module_unavailable_reason,
    report_missed_today,
    root_menu,
    send_time_menu,
    send_time_option,
    standard_send_time,
)
from digest.config import AppConfig, SettingsLevel
from digest.reports.modules import IMPLEMENTED_MODULES


def button_texts(keyboard: InlineKeyboardMarkup) -> list[str]:
    return [button.text for row in keyboard.inline_keyboard for button in row]


def button_for(keyboard: InlineKeyboardMarkup, module_id: str) -> tuple[str, str]:
    for row in keyboard.inline_keyboard:
        for button in row:
            if f" {module_id} · " in button.text:
                assert button.callback_data is not None
                return button.text, button.callback_data
    raise AssertionError(f"нет кнопки {module_id}")


def test_root_menu_offers_three_levels_in_romanian() -> None:
    _, keyboard = root_menu()

    assert button_texts(keyboard) == ["Zilnic", "Săptămânal", "Lunar"]
    assert keyboard.inline_keyboard[0][0].callback_data == LevelMenu(level="daily").pack()


def test_level_menu_marks_enabled_and_disabled_modules_and_targets_opposite_state(
    app_config: AppConfig,
) -> None:
    enabled_by_module = {module_id: True for module_id in app_config.modules.daily} | {"d2": False}

    text, keyboard = level_menu(
        app_config, "daily", enabled_by_module, IMPLEMENTED_MODULES, time(20, 0)
    )

    assert text.startswith("Zilnic · ora 20:00")
    assert button_for(keyboard, "d1") == (
        "✅ d1 · Raport format consilieri",
        ModuleSwitch(module_id="d1", enabled=False).pack(),
    )
    assert button_for(keyboard, "d2") == (
        "⬜ d2 · Lead-uri neatinse azi",
        ModuleSwitch(module_id="d2", enabled=True).pack(),
    )
    assert button_texts(keyboard)[-2:] == ["🕒 Ora", "⬅ Înapoi"]


def test_level_menu_greys_out_unavailable_modules_with_reason(app_config: AppConfig) -> None:
    enabled_by_module = {module_id: False for module_id in app_config.modules.monthly}

    _, keyboard = level_menu(
        app_config, "monthly", enabled_by_module, IMPLEMENTED_MODULES, time(9, 0)
    )

    assert button_for(keyboard, "m1")[0] == "▫️ m1 · Rezumatul lunii · nu e disponibil: sursa B"
    assert button_for(keyboard, "m6")[0] == (
        "▫️ m6 · Clasament SPI · nu e disponibil: KPI necalibrat"
    )
    assert button_for(keyboard, "m7")[0] == "▫️ m7 · Conversie pe sursă · în lucru"


def test_unavailable_reason_lists_all_disconnected_sources(app_config: AppConfig) -> None:
    assert module_unavailable_reason(app_config, "m13", IMPLEMENTED_MODULES) == (
        "nu e disponibil: sursele B, D"
    )
    assert module_unavailable_reason(app_config, "d1", IMPLEMENTED_MODULES) is None


def test_send_time_menu_marks_current_option(app_config: AppConfig) -> None:
    text, keyboard = send_time_menu(app_config, "weekly", time(9, 0))

    assert text == "Săptămânal · ora de trimitere, acum 09:00:"
    assert button_texts(keyboard) == ["08:00", "● 09:00", "10:00", "⬅ Înapoi"]
    assert keyboard.inline_keyboard[0][0].callback_data == (
        SendTimeChoice(level="weekly", hhmm="0800").pack()
    )


def test_send_time_option_accepts_only_configured_times(app_config: AppConfig) -> None:
    assert send_time_option(app_config, "daily", "2000") == time(20, 0)
    assert send_time_option(app_config, "daily", "1800") is None


BUCHAREST = ZoneInfo("Europe/Bucharest")


def test_level_menu_lets_switch_off_enabled_module_that_became_unavailable(
    app_config: AppConfig,
) -> None:
    enabled_by_module = {module_id: False for module_id in app_config.modules.monthly}

    _, keyboard = level_menu(
        app_config, "monthly", enabled_by_module | {"m1": True}, IMPLEMENTED_MODULES, time(9, 0)
    )

    assert button_for(keyboard, "m1") == (
        "✅ m1 · Rezumatul lunii · nu e disponibil: sursa B",
        ModuleSwitch(module_id="m1", enabled=False).pack(),
    )


def test_level_menu_with_nonstandard_schedule_hides_send_time_button(
    app_config: AppConfig,
) -> None:
    enabled_by_module = {module_id: True for module_id in app_config.modules.daily}

    text, keyboard = level_menu(app_config, "daily", enabled_by_module, IMPLEMENTED_MODULES, None)

    assert text.startswith("Zilnic · program nestandard.")
    assert button_texts(keyboard)[-1] == "⬅ Înapoi"
    assert "🕒 Ora" not in button_texts(keyboard)


@pytest.mark.parametrize(
    ("level", "cron", "expected"),
    [
        ("daily", "30 19 * * *", time(19, 30)),
        ("weekly", "0 9 * * mon", time(9, 0)),
        ("daily", "15 18 * * *", None),
        ("daily", "*/5 19 * * *", None),
        ("daily", "0,30 20 * * *", None),
        ("daily", "0 25 * * *", None),
    ],
)
def test_standard_send_time_accepts_only_menu_options(
    app_config: AppConfig, level: SettingsLevel, cron: str, expected: time | None
) -> None:
    assert standard_send_time(app_config, level, cron) == expected


@pytest.mark.parametrize(
    ("old_cron", "new_cron", "now", "expected"),
    [
        # 20:10, перенос 20:30 → 20:00: сегодняшний отчёт ещё не ушёл, новое время прошло.
        ("30 20 * * *", "0 20 * * *", datetime(2026, 9, 25, 20, 10), True),
        # Новое время ещё впереди: планировщик отправит сам.
        ("30 20 * * *", "0 20 * * *", datetime(2026, 9, 25, 19, 45), False),
        # Старое время уже прошло: отчёт за сегодня отправлен.
        ("0 20 * * *", "30 19 * * *", datetime(2026, 9, 25, 20, 10), False),
        # Weekly в пятницу: сегодня не день отправки.
        ("0 10 * * mon", "0 8 * * mon", datetime(2026, 9, 25, 9, 0), False),
        ("0 10 * * mon", "0 8 * * mon", datetime(2026, 9, 28, 9, 0), True),
    ],
)
def test_report_missed_today_only_between_new_and_old_time(
    old_cron: str, new_cron: str, now: datetime, expected: bool
) -> None:
    assert (
        report_missed_today(old_cron, new_cron, now.replace(tzinfo=BUCHAREST), BUCHAREST)
        is expected
    )


def test_cron_with_send_time_keeps_day_fields() -> None:
    assert cron_with_send_time("0 9 * * mon", time(10, 0)) == "0 10 * * mon"
    assert cron_with_send_time("30 19 * * *", time(20, 30)) == "30 20 * * *"
