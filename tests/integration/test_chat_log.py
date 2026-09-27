from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.chat.log import AskedQuestion, questions_since, record_question
from digest.chat.loop import ChatAnswer, ExecutedToolCall
from digest.chat.tools import ToolOutcome
from digest.db.schema import chat_questions
from factories import BUCHAREST

TENANT_ID = "sofabelle"
CHAT_ID = -1002
QUESTION = AskedQuestion(chat_id=CHAT_ID, user_id=5001, message_id=77, text="Câte lead-uri?")
MIDNIGHT = datetime(2026, 9, 23, tzinfo=BUCHAREST)


async def test_answered_question_is_logged_with_tools_and_tokens(engine: AsyncEngine) -> None:
    answer = ChatAnswer(
        text="Au fost 3 lead-uri.",
        status="answered",
        tool_calls=(
            ExecutedToolCall(
                "funnel",
                {"period": "azi", "showroom": "toate"},
                ToolOutcome({}, is_error=False, snapshot_dates=(date(2026, 9, 23),)),
            ),
        ),
        input_tokens=200,
        output_tokens=40,
    )

    await record_question(engine, TENANT_ID, QUESTION, answer.status, answer.text, 850, answer)

    async with engine.connect() as connection:
        row = (await connection.execute(select(chat_questions))).mappings().one()
    assert row["question"] == "Câte lead-uri?"
    assert row["answer"] == "Au fost 3 lead-uri."
    assert row["tool_calls"] == [
        {"name": "funnel", "input": {"period": "azi", "showroom": "toate"}, "ok": True}
    ]
    assert row["snapshot_dates"] == [date(2026, 9, 23)]
    assert (row["input_tokens"], row["output_tokens"], row["duration_ms"]) == (200, 40, 850)
    assert (row["chat_id"], row["user_id"], row["message_id"]) == (CHAT_ID, 5001, 77)


async def test_daily_count_skips_rate_limited_and_earlier_days(engine: AsyncEngine) -> None:
    await record_question(engine, TENANT_ID, QUESTION, "api_error", "Nu pot.", 10)
    await record_question(engine, TENANT_ID, QUESTION, "rate_limited", "Limită.", 1)
    await record_question(engine, TENANT_ID, QUESTION, "no_tool", "Nu.", 10)
    other_chat = AskedQuestion(chat_id=-1001, user_id=1, message_id=1, text="?")
    await record_question(engine, TENANT_ID, other_chat, "no_tool", "Nu.", 10)
    async with engine.begin() as connection:
        await connection.execute(
            update(chat_questions)
            .where(chat_questions.c.status == "no_tool", chat_questions.c.chat_id == CHAT_ID)
            .values(created_at=datetime(2026, 9, 22, 23, 59, tzinfo=BUCHAREST))
        )

    count = await questions_since(engine, TENANT_ID, CHAT_ID, MIDNIGHT)

    assert count == 1
