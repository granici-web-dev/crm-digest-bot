from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from aiogram.types import Chat, Message, MessageEntity, Update, User
from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.bot import chat_handlers
from digest.bot.chat_handlers import DAILY_LIMIT_TEXT, ChatDeps
from digest.bot.dispatcher import bot_dispatcher
from digest.chat.log import AskedQuestion, questions_since, record_question
from digest.chat.loop import CANNOT_ANSWER_NOW_TEXT, UNVERIFIED_NUMBERS_TEXT
from digest.chat.state import store_chat_enabled
from digest.config import AppConfig
from digest.db.schema import chat_questions, lead_snapshots, snapshot_runs
from digest.delivery.ops import OpsChannel
from digest.reports.modules import IMPLEMENTED_MODULES
from digest.reports.runner import ReportDeps
from factories import BUCHAREST, lead_snapshots_row, make_lead_links, make_snapshot_row
from fakes import BOT_USER, ScriptedAnthropic, recording_bot, scripted_anthropic, text_message
from fakes import tool_use_message as tool_use

TENANT_ID = "sofabelle"
TEST_CHAT_ID = -1002
PROD_CHAT_ID = -1001
FOREIGN_CHAT_ID = -1009
OPS_CHAT_ID = -1003
ASKER_ID = 7001
TODAY = date(2026, 9, 23)
MENTION = f"@{BOT_USER.username}"
FUNNEL_TODAY = ("funnel", {"period": "azi", "showroom": "toate"})
REPORT_MESSAGE_ID = 9_001
OTHER_USER_ID = 7002
WEEK_BUCURESTI = ("funnel", {"period": "saptamana_curenta", "showroom": "București"})
WEEK_CLUJ = ("funnel", {"period": "saptamana_curenta", "showroom": "Cluj"})


class ChatHarness:
    def __init__(
        self,
        engine: AsyncEngine,
        config: AppConfig,
        api: ScriptedAnthropic | None,
        chat_ids: frozenset[int] = frozenset({TEST_CHAT_ID}),
    ) -> None:
        report_bot, self.telegram = recording_bot()
        ops_bot, self.ops = recording_bot()
        self.deps = ReportDeps(
            engine=engine,
            config=config,
            tenant_id=TENANT_ID,
            report_bot=report_bot,
            ops=OpsChannel(ops_bot, OPS_CHAT_ID),
            report_chat_id=TEST_CHAT_ID,
            modules=IMPLEMENTED_MODULES,
            lead_links=make_lead_links(config.status_mapping),
        )
        self.api = api
        chat = ChatDeps(None if api is None else api.client, "claude-sonnet-5", chat_ids)
        self.dispatcher = bot_dispatcher(
            self.deps,
            lambda level, cron: None,
            [5001],
            lambda: datetime(2026, 9, 23, 20, 0, tzinfo=BUCHAREST),
            chat,
        )
        self.update_id = 0

    async def send(
        self,
        text: str,
        chat_id: int = TEST_CHAT_ID,
        chat_type: str = "supergroup",
        reply_to_bot_message: int | None = None,
        user_id: int = ASKER_ID,
    ) -> int:
        self.update_id += 1
        entities = (
            [MessageEntity(type="mention", offset=text.index(MENTION), length=len(MENTION))]
            if MENTION in text
            else None
        )
        replied = (
            Message(
                message_id=reply_to_bot_message,
                date=datetime.now(UTC),
                chat=Chat(id=chat_id, type=chat_type),
                from_user=BOT_USER,
                text="Raport zilnic",
            )
            if reply_to_bot_message is not None
            else None
        )
        message_id = 100 + self.update_id
        message = Message(
            message_id=message_id,
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type=chat_type),
            from_user=User(id=user_id, is_bot=False, first_name="Director"),
            text=text,
            entities=entities,
            reply_to_message=replied,
        )
        await self.dispatcher.feed_update(
            self.deps.report_bot, Update(update_id=self.update_id, message=message)
        )
        return message_id

    @property
    def replies(self) -> list[str]:
        return [message.text for message in self.telegram.sent]

    @property
    def ops_texts(self) -> list[str]:
        return [message.text for message in self.ops.sent]


async def insert_snapshot(engine: AsyncEngine, snapshot_date: date) -> None:
    leads = [
        make_snapshot_row(
            lead_id=lead_id,
            created_at=datetime(2026, 9, 23, 11, tzinfo=BUCHAREST),
            last_contact_at=datetime(2026, 9, 23, 11, tzinfo=BUCHAREST),
        )
        for lead_id in (1, 2, 3)
    ]
    async with engine.begin() as connection:
        for lead in leads:
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


async def logged_questions(engine: AsyncEngine) -> list[dict[str, Any]]:
    async with engine.connect() as connection:
        result = await connection.execute(select(chat_questions).order_by(chat_questions.c.id))
        return [dict(row) for row in result.mappings()]


@pytest.fixture
async def enabled_chat(engine: AsyncEngine) -> AsyncEngine:
    await store_chat_enabled(engine, TENANT_ID, True, updated_by=5001)
    await insert_snapshot(engine, TODAY)
    return engine


async def test_mention_in_test_group_gets_reply_and_log_row(
    enabled_chat: AsyncEngine, app_config: AppConfig
) -> None:
    api = scripted_anthropic(tool_use(FUNNEL_TODAY), text_message("Azi au fost 3 lead-uri."))
    harness = ChatHarness(enabled_chat, app_config, api)

    message_id = await harness.send(f"{MENTION} câte lead-uri azi?")

    [reply] = harness.telegram.sent
    assert reply.chat_id == TEST_CHAT_ID
    assert reply.reply_to_message_id == message_id
    assert reply.text == ("Azi au fost 3 lead-uri.\n\n<i>Perioada: 23.09.2026 (azi) · funnel()</i>")
    assert api.requests[0]["messages"][0]["content"] == "câte lead-uri azi?"
    [row] = await logged_questions(enabled_chat)
    assert row["status"] == "answered"
    assert row["question"] == "câte lead-uri azi?"
    assert row["answer"] == reply.text
    assert row["tool_calls"] == [{"name": "funnel", "input": FUNNEL_TODAY[1], "ok": True}]
    assert row["snapshot_dates"] == [TODAY]
    assert (row["chat_id"], row["user_id"], row["message_id"]) == (
        TEST_CHAT_ID,
        ASKER_ID,
        message_id,
    )
    assert (row["input_tokens"], row["output_tokens"]) == (200, 40)
    assert harness.ops_texts == []


async def test_reply_to_bot_message_triggers(
    enabled_chat: AsyncEngine, app_config: AppConfig
) -> None:
    api = scripted_anthropic(text_message("Pot răspunde doar despre date."))
    harness = ChatHarness(enabled_chat, app_config, api)

    await harness.send("și ieri?", reply_to_bot_message=REPORT_MESSAGE_ID)

    assert harness.replies == ["Pot răspunde doar despre date."]


@pytest.mark.parametrize(
    ("chat_id", "chat_type"),
    [(ASKER_ID, "private"), (FOREIGN_CHAT_ID, "supergroup")],
)
async def test_private_chat_and_foreign_group_are_silent(
    enabled_chat: AsyncEngine, app_config: AppConfig, chat_id: int, chat_type: str
) -> None:
    api = scripted_anthropic()
    harness = ChatHarness(enabled_chat, app_config, api)

    await harness.send(f"{MENTION} câte lead-uri?", chat_id=chat_id, chat_type=chat_type)

    assert harness.replies == []
    assert api.requests == []
    assert await logged_questions(enabled_chat) == []


async def test_prod_group_is_silent_in_dry_run(
    enabled_chat: AsyncEngine, app_config: AppConfig
) -> None:
    api = scripted_anthropic()
    # DRY_RUN=1: в chat_ids только тестовая группа (digest.app.chat_ids).
    harness = ChatHarness(enabled_chat, app_config, api, chat_ids=frozenset({TEST_CHAT_ID}))

    await harness.send(f"{MENTION} câte lead-uri?", chat_id=PROD_CHAT_ID)

    assert harness.replies == []
    assert api.requests == []


async def test_message_without_mention_is_silent_and_not_logged(
    enabled_chat: AsyncEngine, app_config: AppConfig
) -> None:
    api = scripted_anthropic()
    harness = ChatHarness(enabled_chat, app_config, api)

    await harness.send("câte lead-uri azi? @alt_bot")

    assert harness.replies == []
    assert api.requests == []
    assert await logged_questions(enabled_chat) == []


async def test_chat_disabled_is_silent(engine: AsyncEngine, app_config: AppConfig) -> None:
    api = scripted_anthropic()
    harness = ChatHarness(engine, app_config, api)

    await harness.send(f"{MENTION} câte lead-uri?")

    assert harness.replies == []
    assert api.requests == []


async def test_enabled_chat_without_api_key_is_silent(
    enabled_chat: AsyncEngine, app_config: AppConfig
) -> None:
    harness = ChatHarness(enabled_chat, app_config, api=None)

    await harness.send(f"{MENTION} câte lead-uri?")

    assert harness.replies == []
    assert await logged_questions(enabled_chat) == []


async def test_question_over_daily_limit_is_refused(
    enabled_chat: AsyncEngine, app_config: AppConfig
) -> None:
    limit = app_config.modules.chat.daily_question_limit
    asked = AskedQuestion(chat_id=TEST_CHAT_ID, user_id=ASKER_ID, message_id=1, text="?")
    for _ in range(limit):
        await record_question(enabled_chat, TENANT_ID, asked, "no_tool", "Nu.", 10)
    api = scripted_anthropic()
    harness = ChatHarness(enabled_chat, app_config, api)

    await harness.send(f"{MENTION} ce faci?")

    assert api.requests == []
    assert harness.replies == [DAILY_LIMIT_TEXT]
    statuses = [row["status"] for row in await logged_questions(enabled_chat)]
    assert statuses == ["no_tool"] * limit + ["rate_limited"]


async def test_question_below_daily_limit_is_answered(
    enabled_chat: AsyncEngine, app_config: AppConfig
) -> None:
    limit = app_config.modules.chat.daily_question_limit
    asked = AskedQuestion(chat_id=TEST_CHAT_ID, user_id=ASKER_ID, message_id=1, text="?")
    for _ in range(limit - 1):
        await record_question(enabled_chat, TENANT_ID, asked, "no_tool", "Nu.", 10)
    await record_question(enabled_chat, TENANT_ID, asked, "rate_limited", "Limită.", 1)
    harness = ChatHarness(enabled_chat, app_config, scripted_anthropic(text_message("Nu știu.")))

    await harness.send(f"{MENTION} ce faci?")

    assert harness.replies == ["Nu știu."]


async def test_api_failure_answers_cannot_answer_now_and_alerts_ops(
    enabled_chat: AsyncEngine, app_config: AppConfig
) -> None:
    harness = ChatHarness(enabled_chat, app_config, scripted_anthropic(529))

    await harness.send(f"{MENTION} câte lead-uri?")

    assert harness.replies == [CANNOT_ANSWER_NOW_TEXT]
    [alert] = harness.ops_texts
    assert alert.startswith("Chat: API Anthropic недоступен")
    assert "câte lead-uri?" in alert
    [row] = await logged_questions(enabled_chat)
    assert row["status"] == "api_error"


async def test_question_over_deadline_answers_cannot_answer_now_and_counts(
    enabled_chat: AsyncEngine, app_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat_handlers, "QUESTION_DEADLINE_SECONDS", 0)
    api = scripted_anthropic(tool_use(FUNNEL_TODAY), text_message("Azi au fost 3 lead-uri."))
    harness = ChatHarness(enabled_chat, app_config, api)

    await harness.send(f"{MENTION} câte lead-uri azi?")

    assert harness.replies == [CANNOT_ANSWER_NOW_TEXT]
    [alert] = harness.ops_texts
    assert alert == "Chat: ответ не уложился в 0 с. Вопрос: câte lead-uri azi?."
    [row] = await logged_questions(enabled_chat)
    assert row["status"] == "api_error"
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    assert await questions_since(enabled_chat, TENANT_ID, row["chat_id"], epoch) == 1


async def test_unverified_number_is_blocked_and_alert_names_it(
    enabled_chat: AsyncEngine, app_config: AppConfig
) -> None:
    api = scripted_anthropic(
        tool_use(FUNNEL_TODAY),
        text_message("Azi au fost 3 lead-uri din 17 posibile."),
        text_message("Azi au fost 3 lead-uri, 17 posibile."),
    )
    harness = ChatHarness(enabled_chat, app_config, api)

    await harness.send(f"{MENTION} câte lead-uri azi?")

    assert harness.replies == [UNVERIFIED_NUMBERS_TEXT]
    [alert] = harness.ops_texts
    assert "«17»" in alert
    assert "повтор не помог" in alert
    assert "câte lead-uri azi?" in alert
    assert "funnel()" in alert
    assert alert.endswith("Текст модели: Azi au fost 3 lead-uri din 17 posibile.")
    [row] = await logged_questions(enabled_chat)
    assert row["status"] == "unverified_numbers"


async def test_guard_retry_answer_reaches_group_and_ops_gets_rejected_text(
    enabled_chat: AsyncEngine, app_config: AppConfig
) -> None:
    rejected_text = "Azi au fost 3 lead-uri din 17 posibile. " + "Detalii. " * 80
    api = scripted_anthropic(
        tool_use(FUNNEL_TODAY),
        text_message(rejected_text),
        text_message("Azi au fost 3 lead-uri."),
    )
    harness = ChatHarness(enabled_chat, app_config, api)

    await harness.send(f"{MENTION} câte lead-uri azi?")

    [reply] = harness.replies
    assert reply.startswith("Azi au fost 3 lead-uri.\n\n")
    [alert] = harness.ops_texts
    assert "страж отклонил число «17», повтор прошёл, ответ отправлен" in alert
    assert alert.endswith("Текст модели: " + rejected_text.strip()[:500])
    [row] = await logged_questions(enabled_chat)
    assert row["status"] == "answered"


async def test_numbers_without_tool_are_not_sent_to_group(
    enabled_chat: AsyncEngine, app_config: AppConfig
) -> None:
    api = scripted_anthropic(text_message("Cred că au fost 40 de lead-uri."))
    harness = ChatHarness(enabled_chat, app_config, api)

    await harness.send(f"{MENTION} câte lead-uri?")

    [reply] = harness.replies
    assert "40" not in reply
    [row] = await logged_questions(enabled_chat)
    assert row["status"] == "blocked_numbers"


async def age_questions(engine: AsyncEngine, minutes: int) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            update(chat_questions).values(
                created_at=chat_questions.c.created_at - timedelta(minutes=minutes)
            )
        )


async def ask_about_bucuresti(harness: ChatHarness) -> None:
    await harness.send(f"{MENTION} câte lead-uri am avut săptămâna aceasta în București?")


def bucuresti_script(*follow_up: dict[str, Any]) -> ScriptedAnthropic:
    return scripted_anthropic(
        tool_use(WEEK_BUCURESTI), text_message("În București: 3 lead-uri."), *follow_up
    )


def first_user_turn(api: ScriptedAnthropic, request_index: int) -> Any:
    return api.requests[request_index]["messages"][0]["content"]


async def test_follow_up_within_ten_minutes_reuses_period_and_changes_showroom(
    enabled_chat: AsyncEngine, app_config: AppConfig
) -> None:
    api = bucuresti_script(tool_use(WEEK_CLUJ), text_message("În Cluj: 0 lead-uri."))
    harness = ChatHarness(enabled_chat, app_config, api)
    await ask_about_bucuresti(harness)
    await age_questions(enabled_chat, 2)

    await harness.send(f"{MENTION} Dar Cluj?")

    context, question = first_user_turn(api, 2)
    assert question == {"type": "text", "text": "Dar Cluj?"}
    assert "săptămâna aceasta în București" in context["text"]
    assert 'funnel {"period": "saptamana_curenta", "showroom": "București"}' in context["text"]
    assert "3 lead-uri" not in context["text"]
    first, follow_up = await logged_questions(enabled_chat)
    assert follow_up["tool_calls"] == [{"name": "funnel", "input": WEEK_CLUJ[1], "ok": True}]
    assert follow_up["status"] == "answered"
    assert follow_up["context_question_id"] == first["id"]
    assert first["context_question_id"] is None


async def test_follow_up_after_eleven_minutes_has_no_context(
    enabled_chat: AsyncEngine, app_config: AppConfig
) -> None:
    api = bucuresti_script(text_message("Despre ce perioadă și ce întrebare?"))
    harness = ChatHarness(enabled_chat, app_config, api)
    await ask_about_bucuresti(harness)
    await age_questions(enabled_chat, 11)

    await harness.send(f"{MENTION} Dar Cluj?")

    assert first_user_turn(api, 2) == "Dar Cluj?"
    assert (await logged_questions(enabled_chat))[1]["context_question_id"] is None


async def test_question_of_another_user_is_not_context(
    enabled_chat: AsyncEngine, app_config: AppConfig
) -> None:
    api = bucuresti_script(text_message("Despre ce perioadă?"))
    harness = ChatHarness(enabled_chat, app_config, api)
    await ask_about_bucuresti(harness)

    await harness.send(f"{MENTION} Dar Cluj?", user_id=OTHER_USER_ID)

    assert first_user_turn(api, 2) == "Dar Cluj?"


async def test_reply_to_bot_answer_uses_that_question_regardless_of_age(
    enabled_chat: AsyncEngine, app_config: AppConfig
) -> None:
    api = bucuresti_script(tool_use(WEEK_CLUJ), text_message("În Cluj: 0 lead-uri."))
    harness = ChatHarness(enabled_chat, app_config, api)
    await ask_about_bucuresti(harness)
    await age_questions(enabled_chat, 180)
    [first] = await logged_questions(enabled_chat)

    await harness.send(
        "Dar Cluj?", reply_to_bot_message=first["reply_message_id"], user_id=OTHER_USER_ID
    )

    context, _ = first_user_turn(api, 2)
    assert "București" in context["text"]
    assert (await logged_questions(enabled_chat))[1]["context_question_id"] == first["id"]


async def test_reply_to_bot_report_falls_back_to_recent_question(
    enabled_chat: AsyncEngine, app_config: AppConfig
) -> None:
    api = bucuresti_script(tool_use(WEEK_CLUJ), text_message("În Cluj: 0 lead-uri."))
    harness = ChatHarness(enabled_chat, app_config, api)
    await ask_about_bucuresti(harness)

    await harness.send("Dar Cluj?", reply_to_bot_message=REPORT_MESSAGE_ID)

    context, _ = first_user_turn(api, 2)
    assert "București" in context["text"]


async def test_reply_message_id_is_logged(enabled_chat: AsyncEngine, app_config: AppConfig) -> None:
    harness = ChatHarness(enabled_chat, app_config, bucuresti_script())

    await ask_about_bucuresti(harness)

    [reply] = harness.telegram.sent
    [row] = await logged_questions(enabled_chat)
    assert row["reply_message_id"] == reply.message_id
