import asyncio
from collections.abc import AsyncIterator, Callable
from datetime import UTC, date, datetime, time
from functools import partial
from typing import Any

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText
from aiogram.types import CallbackQuery, Chat, InaccessibleMessage, Message, Update, User
from anthropic import AsyncAnthropic
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.app import schedule_report_job, seed_defaults, stored_schedules
from digest.bot.chat_handlers import ChatDeps
from digest.bot.dispatcher import HANDLER_ERROR_TEXT, bot_dispatcher
from digest.bot.settings_handlers import STALE_MENU_TEXT
from digest.bot.settings_menu import (
    ChatMenu,
    ChatSwitch,
    LevelMenu,
    ModuleSwitch,
    SendTimeChoice,
    SendTimeMenu,
)
from digest.config import AppConfig
from digest.db.schema import (
    lead_snapshots,
    module_settings,
    schedules,
    settings,
    snapshot_runs,
)
from digest.delivery.ops import OpsChannel
from digest.reports.modules import IMPLEMENTED_MODULES
from digest.reports.periods import ReportLevel
from digest.reports.runner import ReportDeps, run_report
from factories import BUCHAREST, lead_snapshots_row, make_lead_links, make_snapshot_row
from fakes import recording_bot, scripted_anthropic

TENANT_ID = "sofabelle"
GROUP_CHAT_ID = -1001
OPS_CHAT_ID = -1003
ADMIN_ID = 5001
STRANGER_ID = 6001
MENU_MESSAGE_ID = 10


class SettingsHarness:
    def __init__(
        self,
        engine: AsyncEngine,
        config: AppConfig,
        anthropic_client: AsyncAnthropic | None = None,
    ) -> None:
        report_bot, self.chat = recording_bot()
        ops_bot, self.ops = recording_bot()
        self.deps = ReportDeps(
            engine=engine,
            config=config,
            tenant_id=TENANT_ID,
            report_bot=report_bot,
            ops=OpsChannel(ops_bot, OPS_CHAT_ID),
            report_chat_id=GROUP_CHAT_ID,
            modules=IMPLEMENTED_MODULES,
            lead_links=make_lead_links(config.status_mapping),
        )
        self.scheduler = AsyncIOScheduler(timezone=BUCHAREST)
        self.reschedule: Callable[[ReportLevel, str], None] = partial(
            schedule_report_job, self.scheduler, self.deps, None
        )
        # До любого варианта daily и в пятницу: перенос времени не досылает отчёт, пока тест
        # сам не выставит момент.
        self.now = datetime(2026, 9, 25, 12, 0, tzinfo=BUCHAREST)
        self.dispatcher = bot_dispatcher(
            self.deps,
            lambda level, cron: self.reschedule(level, cron),
            [ADMIN_ID],
            lambda: self.now,
            ChatDeps(anthropic_client, "claude-sonnet-5", frozenset({GROUP_CHAT_ID})),
        )
        self.update_id = 0

    async def feed(self, **update_fields: Any) -> None:
        self.update_id += 1
        update = Update(update_id=self.update_id, **update_fields)
        await self.dispatcher.feed_update(self.deps.report_bot, update)

    async def send_text(
        self, text: str, user_id: int = ADMIN_ID, chat_id: int | None = None
    ) -> None:
        chat = (
            Chat(id=user_id, type="private") if chat_id is None else Chat(id=chat_id, type="group")
        )
        await self.feed(
            message=Message(
                message_id=1,
                date=datetime.now(UTC),
                chat=chat,
                from_user=admin_user(user_id),
                text=text,
            )
        )

    async def press(
        self, callback_data: str, user_id: int = ADMIN_ID, menu_is_stale: bool = False
    ) -> None:
        chat = Chat(id=user_id, type="private")
        message: Message | InaccessibleMessage = (
            InaccessibleMessage(chat=chat, message_id=MENU_MESSAGE_ID)
            if menu_is_stale
            else Message(message_id=MENU_MESSAGE_ID, date=datetime.now(UTC), chat=chat, text="menu")
        )
        await self.feed(
            callback_query=CallbackQuery(
                id=f"callback-{self.update_id}",
                from_user=admin_user(user_id),
                chat_instance="settings",
                data=callback_data,
                message=message,
            )
        )

    @property
    def ops_texts(self) -> list[str]:
        return [message.text for message in self.ops.sent]

    @property
    def last_answer_text(self) -> str | None:
        return self.chat.callback_answers[-1].text


def admin_user(user_id: int) -> User:
    return User(id=user_id, is_bot=False, first_name="Ana", last_name="Admin")


@pytest.fixture
async def harness(engine: AsyncEngine, app_config: AppConfig) -> AsyncIterator[SettingsHarness]:
    await seed_defaults(engine, app_config, TENANT_ID)
    harness = SettingsHarness(engine, app_config)
    harness.scheduler.start(paused=True)
    for level, schedule in (await stored_schedules(engine, TENANT_ID)).items():
        if schedule.enabled:
            harness.reschedule(level, schedule.cron)
    yield harness
    harness.scheduler.shutdown(wait=False)


async def module_settings_rows(engine: AsyncEngine) -> list[tuple[str, bool, int | None]]:
    async with engine.connect() as connection:
        result = await connection.execute(
            select(
                module_settings.c.module_id, module_settings.c.enabled, module_settings.c.updated_by
            )
        )
        return [(module_id, enabled, updated_by) for module_id, enabled, updated_by in result]


async def schedule_row(engine: AsyncEngine, level: str) -> tuple[str, int | None]:
    async with engine.connect() as connection:
        cron, updated_by = (
            await connection.execute(
                select(schedules.c.cron, schedules.c.updated_by).where(
                    schedules.c.report_level == level
                )
            )
        ).one()
        return cron, updated_by


async def store_successful_snapshot(engine: AsyncEngine, snapshot_date: date) -> None:
    async with engine.begin() as connection:
        lead = make_snapshot_row(
            lead_id=1, created_at=datetime.combine(snapshot_date, time(11, 0), tzinfo=BUCHAREST)
        )
        await connection.execute(
            insert(lead_snapshots).values(
                tenant_id=TENANT_ID, snapshot_date=snapshot_date, **lead_snapshots_row(lead)
            )
        )
        await connection.execute(
            insert(snapshot_runs).values(
                tenant_id=TENANT_ID,
                snapshot_date=snapshot_date,
                attempt=1,
                status="success",
                trigger="scheduled",
            )
        )


async def set_cron(engine: AsyncEngine, level: str, cron: str) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            update(schedules).where(schedules.c.report_level == level).values(cron=cron)
        )


def trigger_fields(harness: SettingsHarness, level: str) -> dict[str, str]:
    job = harness.scheduler.get_job(f"report_{level}")
    assert job is not None
    assert isinstance(job.trigger, CronTrigger)
    return {field.name: str(field) for field in job.trigger.fields}


async def test_stranger_private_message_gets_no_reply(harness: SettingsHarness) -> None:
    await harness.send_text("/settings", user_id=STRANGER_ID)

    assert harness.chat.request_count == 0


async def test_admin_in_group_gets_no_reply(harness: SettingsHarness) -> None:
    await harness.send_text("/settings", chat_id=GROUP_CHAT_ID)

    assert harness.chat.request_count == 0


async def test_stranger_callback_changes_nothing(harness: SettingsHarness) -> None:
    await harness.press(ModuleSwitch(module_id="d2", enabled=False).pack(), user_id=STRANGER_ID)

    assert harness.chat.request_count == 0
    assert await module_settings_rows(harness.deps.engine) == []
    assert harness.ops_texts == []


@pytest.mark.parametrize("command", ["/settings", "/start"])
async def test_admin_command_shows_levels_menu(harness: SettingsHarness, command: str) -> None:
    await harness.send_text(command)

    [reply] = harness.chat.sent
    assert reply.chat_id == ADMIN_ID
    assert reply.text == "Setări rapoarte. Alegeți raportul:"
    assert reply.reply_markup is not None
    assert [row[0].text for row in reply.reply_markup.inline_keyboard] == [
        "Zilnic",
        "Săptămânal",
        "Lunar",
        "Chat",
    ]


async def test_level_button_shows_modules_and_send_time(harness: SettingsHarness) -> None:
    await harness.press(LevelMenu(level="daily").pack())

    [edited] = harness.chat.edited
    assert edited.message_id == MENU_MESSAGE_ID
    assert edited.text.startswith("Zilnic · ora 19:30")


async def test_switch_writes_module_settings_with_admin_id_and_reports_to_ops(
    harness: SettingsHarness,
) -> None:
    await harness.press(ModuleSwitch(module_id="d2", enabled=False).pack())

    assert await module_settings_rows(harness.deps.engine) == [("d2", False, ADMIN_ID)]
    assert harness.ops_texts == [
        f"Настройки: Ana Admin ({ADMIN_ID}): d2 untouched_leads включён → выключен"
    ]
    [edited] = harness.chat.edited
    assert edited.reply_markup is not None
    texts = [row[0].text for row in edited.reply_markup.inline_keyboard]
    assert "⬜ d2 · Lead-uri neatinse azi" in texts


async def test_switch_back_updates_existing_row(harness: SettingsHarness) -> None:
    await harness.press(ModuleSwitch(module_id="d2", enabled=False).pack())
    await harness.press(ModuleSwitch(module_id="d2", enabled=True).pack())

    assert await module_settings_rows(harness.deps.engine) == [("d2", True, ADMIN_ID)]
    assert harness.ops_texts[-1].endswith("выключен → включён")


async def test_repeated_target_state_is_noop(harness: SettingsHarness) -> None:
    await harness.press(ModuleSwitch(module_id="d2", enabled=False).pack())
    await harness.press(ModuleSwitch(module_id="d2", enabled=False).pack())

    assert len(harness.ops_texts) == 1
    assert len(harness.chat.edited) == 1
    assert len(harness.chat.callback_answers) == 2


@pytest.mark.parametrize(
    ("module_id", "reason"),
    [
        ("m1", "nu e disponibil: sursa B"),
        ("m6", "nu e disponibil: KPI necalibrat"),
        ("d8", "în lucru"),
    ],
)
async def test_unavailable_module_is_not_switched(
    harness: SettingsHarness, module_id: str, reason: str
) -> None:
    await harness.press(ModuleSwitch(module_id=module_id, enabled=True).pack())

    assert await module_settings_rows(harness.deps.engine) == []
    assert harness.ops_texts == []
    assert harness.last_answer_text == reason
    assert harness.chat.edited == []


async def test_module_outside_menu_levels_is_ignored(harness: SettingsHarness) -> None:
    await harness.press(ModuleSwitch(module_id="y1", enabled=True).pack())

    assert await module_settings_rows(harness.deps.engine) == []
    assert harness.ops_texts == []


async def test_send_time_change_writes_schedule_and_reschedules_job(
    harness: SettingsHarness,
) -> None:
    await harness.press(SendTimeChoice(level="daily", hhmm="2000").pack())

    assert await schedule_row(harness.deps.engine, "daily") == ("0 20 * * *", ADMIN_ID)
    fields = trigger_fields(harness, "daily")
    assert (fields["hour"], fields["minute"]) == ("20", "0")
    assert harness.ops_texts == [f"Настройки: Ana Admin ({ADMIN_ID}): время daily 19:30 → 20:00"]
    [edited] = harness.chat.edited
    assert edited.reply_markup is not None
    assert [row[0].text for row in edited.reply_markup.inline_keyboard][:3] == [
        "19:30",
        "● 20:00",
        "20:30",
    ]


async def test_weekly_send_time_change_keeps_monday(harness: SettingsHarness) -> None:
    await harness.press(SendTimeChoice(level="weekly", hhmm="1000").pack())

    assert await schedule_row(harness.deps.engine, "weekly") == ("0 10 * * mon", ADMIN_ID)
    fields = trigger_fields(harness, "weekly")
    assert (fields["hour"], fields["day_of_week"]) == ("10", "mon")


async def test_send_time_outside_options_is_rejected(harness: SettingsHarness) -> None:
    await harness.press(SendTimeChoice(level="daily", hhmm="1800").pack())

    assert await schedule_row(harness.deps.engine, "daily") == ("30 19 * * *", None)
    assert trigger_fields(harness, "daily")["hour"] == "19"
    assert harness.ops_texts == []


async def test_runner_skips_module_switched_off_in_settings(harness: SettingsHarness) -> None:
    await store_successful_snapshot(harness.deps.engine, date(2026, 9, 25))
    await harness.press(ModuleSwitch(module_id="d1", enabled=False).pack())
    outcome = await run_report(
        "daily", datetime(2026, 9, 25, 19, 30, tzinfo=BUCHAREST), harness.deps, late=False
    )

    assert outcome == "success"
    group_text = "\n".join(message.text for message in harness.chat.sent)
    assert "<b>TOTAL</b>" not in group_text
    assert "Lead-uri azi:" in group_text


async def test_enabled_module_that_became_unavailable_can_be_switched_off(
    harness: SettingsHarness,
) -> None:
    async with harness.deps.engine.begin() as connection:
        await connection.execute(
            insert(module_settings).values(tenant_id=TENANT_ID, module_id="m1", enabled=True)
        )

    await harness.press(ModuleSwitch(module_id="m1", enabled=False).pack())

    assert await module_settings_rows(harness.deps.engine) == [("m1", False, ADMIN_ID)]
    assert harness.ops_texts == [
        f"Настройки: Ana Admin ({ADMIN_ID}): m1 month_summary_image включён → выключен"
    ]


async def test_earlier_time_already_passed_sends_todays_report_once(
    harness: SettingsHarness,
) -> None:
    await store_successful_snapshot(harness.deps.engine, date(2026, 9, 25))
    await harness.press(SendTimeChoice(level="daily", hhmm="2030").pack())
    harness.now = datetime(2026, 9, 25, 20, 10, tzinfo=BUCHAREST)

    await harness.press(SendTimeChoice(level="daily", hhmm="2000").pack())

    group_messages = [message for message in harness.chat.sent if message.chat_id == GROUP_CHAT_ID]
    admin_messages = [message.text for message in harness.chat.sent if message.chat_id == ADMIN_ID]
    assert group_messages
    assert admin_messages == ["raportul de azi a fost trimis acum"]
    assert harness.ops_texts[-1] == "Настройки: daily: raportul de azi a fost trimis acum"

    await harness.press(SendTimeChoice(level="daily", hhmm="2030").pack())
    await harness.press(SendTimeChoice(level="daily", hhmm="1930").pack())

    assert [message for message in harness.chat.sent if message.chat_id == GROUP_CHAT_ID] == (
        group_messages
    )
    assert [message.text for message in harness.chat.sent if message.chat_id == ADMIN_ID] == (
        admin_messages
    )


async def test_nonstandard_schedule_is_shown_and_not_changed(harness: SettingsHarness) -> None:
    await set_cron(harness.deps.engine, "daily", "*/5 19 * * *")

    await harness.press(LevelMenu(level="daily").pack())
    await harness.press(SendTimeMenu(level="daily").pack())
    await harness.press(SendTimeChoice(level="daily", hhmm="2000").pack())

    assert harness.chat.edited[0].text.startswith("Zilnic · program nestandard.")
    assert [answer.text for answer in harness.chat.callback_answers[1:]] == [
        "program nestandard",
        "program nestandard",
    ]
    assert await schedule_row(harness.deps.engine, "daily") == ("*/5 19 * * *", None)
    assert harness.ops_texts == []


async def test_failed_reschedule_keeps_schedule_and_alerts_ops(harness: SettingsHarness) -> None:
    def failing_reschedule(level: ReportLevel, cron: str) -> None:
        raise RuntimeError("scheduler stopped")

    harness.reschedule = failing_reschedule

    await harness.press(SendTimeChoice(level="daily", hhmm="2000").pack())

    assert await schedule_row(harness.deps.engine, "daily") == ("30 19 * * *", None)
    assert harness.ops_texts == ["/settings: хендлер упал: RuntimeError."]
    assert harness.last_answer_text == HANDLER_ERROR_TEXT


async def test_concurrent_send_time_changes_leave_job_matching_schedule(
    harness: SettingsHarness,
) -> None:
    await asyncio.gather(
        harness.press(SendTimeChoice(level="daily", hhmm="2000").pack()),
        harness.press(SendTimeChoice(level="daily", hhmm="2030").pack()),
    )

    cron, _ = await schedule_row(harness.deps.engine, "daily")
    fields = trigger_fields(harness, "daily")
    assert cron == f"{fields['minute']} {fields['hour']} * * *"


async def test_button_on_menu_older_than_48_hours_asks_for_new_menu(
    harness: SettingsHarness,
) -> None:
    await harness.press(LevelMenu(level="daily").pack(), menu_is_stale=True)

    assert harness.chat.edited == []
    assert harness.last_answer_text == STALE_MENU_TEXT


async def test_send_time_of_disabled_schedule_is_written_without_job(
    harness: SettingsHarness,
) -> None:
    async with harness.deps.engine.begin() as connection:
        await connection.execute(
            update(schedules).where(schedules.c.report_level == "daily").values(enabled=False)
        )
    harness.scheduler.remove_job("report_daily")

    await harness.press(SendTimeChoice(level="daily", hhmm="2000").pack())

    assert await schedule_row(harness.deps.engine, "daily") == ("0 20 * * *", ADMIN_ID)
    assert harness.scheduler.get_job("report_daily") is None
    assert harness.ops_texts == [f"Настройки: Ana Admin ({ADMIN_ID}): время daily 19:30 → 20:00"]


async def test_handler_error_goes_to_ops_and_answers_button(harness: SettingsHarness) -> None:
    harness.chat.fail_next(
        TelegramBadRequest(
            method=EditMessageText(text="x"), message="Bad Request: message can't be edited"
        )
    )

    await harness.press(LevelMenu(level="daily").pack())

    assert harness.ops_texts == ["/settings: хендлер упал: TelegramBadRequest."]
    assert harness.last_answer_text == HANDLER_ERROR_TEXT


async def chat_setting_rows(engine: AsyncEngine) -> list[tuple[str, object, int | None]]:
    async with engine.connect() as connection:
        result = await connection.execute(
            select(settings.c.key, settings.c.value, settings.c.updated_by)
        )
        return [(key, value, updated_by) for key, value, updated_by in result]


async def test_chat_toggle_writes_setting_and_reports_to_ops(
    engine: AsyncEngine, app_config: AppConfig
) -> None:
    harness = SettingsHarness(engine, app_config, scripted_anthropic().client)

    await harness.press(ChatMenu().pack())
    await harness.press(ChatSwitch(enabled=True).pack())

    assert await chat_setting_rows(engine) == [("chat_enabled", True, ADMIN_ID)]
    assert harness.ops_texts == [f"Настройки: Ana Admin ({ADMIN_ID}): Chat выключен → включён"]
    last_menu = harness.chat.edited[-1].reply_markup
    assert last_menu is not None
    assert last_menu.inline_keyboard[0][0].text == "✅ Chat · activ"


async def test_chat_toggle_without_api_key_is_unavailable(
    engine: AsyncEngine, app_config: AppConfig
) -> None:
    harness = SettingsHarness(engine, app_config)

    await harness.press(ChatMenu().pack())
    await harness.press(ChatSwitch(enabled=True).pack())

    assert await chat_setting_rows(engine) == []
    assert harness.ops_texts == []
    assert harness.last_answer_text == "Chat: fără cheie API"
    menu = harness.chat.edited[0].reply_markup
    assert menu is not None
    assert menu.inline_keyboard[0][0].text == "▫️ Chat · fără cheie API"
