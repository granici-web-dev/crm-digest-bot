from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from functools import partial
from typing import Any

import pytest
from aiogram import Dispatcher
from aiogram.types import CallbackQuery, Chat, Message, Update, User
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.app import enabled_schedules, schedule_report_job, seed_defaults
from digest.bot.settings_handlers import settings_router
from digest.bot.settings_menu import LevelMenu, ModuleSwitch, SendTimeChoice
from digest.config import AppConfig
from digest.db.schema import lead_snapshots, module_settings, schedules, snapshot_runs
from digest.delivery.ops import OpsChannel
from digest.reports.modules import IMPLEMENTED_MODULES
from digest.reports.runner import ReportDeps, run_report
from factories import BUCHAREST, lead_snapshots_row, make_snapshot_row
from fakes import recording_bot

TENANT_ID = "sofabelle"
GROUP_CHAT_ID = -1001
OPS_CHAT_ID = -1003
ADMIN_ID = 5001
STRANGER_ID = 6001
MENU_MESSAGE_ID = 10


class SettingsHarness:
    def __init__(self, engine: AsyncEngine, config: AppConfig) -> None:
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
        )
        self.scheduler = AsyncIOScheduler(timezone=BUCHAREST)
        self.reschedule = partial(schedule_report_job, self.scheduler, self.deps, None)
        self.dispatcher = Dispatcher(deps=self.deps, reschedule=self.reschedule)
        self.dispatcher.include_router(settings_router([ADMIN_ID]))
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

    async def press(self, callback_data: str, user_id: int = ADMIN_ID) -> None:
        await self.feed(
            callback_query=CallbackQuery(
                id=f"callback-{self.update_id}",
                from_user=admin_user(user_id),
                chat_instance="settings",
                data=callback_data,
                message=Message(
                    message_id=MENU_MESSAGE_ID,
                    date=datetime.now(UTC),
                    chat=Chat(id=user_id, type="private"),
                    text="menu",
                ),
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
    await seed_defaults(engine, TENANT_ID)
    harness = SettingsHarness(engine, app_config)
    harness.scheduler.start(paused=True)
    for level, cron in (await enabled_schedules(engine, TENANT_ID)).items():
        harness.reschedule(level, cron)
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
        ("d7", "în lucru"),
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
    report_date = date(2026, 9, 25)
    async with harness.deps.engine.begin() as connection:
        lead = make_snapshot_row(
            lead_id=1, created_at=datetime(2026, 9, 25, 11, 0, tzinfo=BUCHAREST)
        )
        await connection.execute(
            insert(lead_snapshots).values(
                tenant_id=TENANT_ID, snapshot_date=report_date, **lead_snapshots_row(lead)
            )
        )
        await connection.execute(
            insert(snapshot_runs).values(
                tenant_id=TENANT_ID, snapshot_date=report_date, attempt=1, status="success"
            )
        )

    await harness.press(ModuleSwitch(module_id="d1", enabled=False).pack())
    outcome = await run_report(
        "daily", datetime(2026, 9, 25, 19, 30, tzinfo=BUCHAREST), harness.deps
    )

    assert outcome == "success"
    group_text = "\n".join(message.text for message in harness.chat.sent)
    assert "<b>TOTAL</b>" not in group_text
    assert "Lead-uri azi:" in group_text
