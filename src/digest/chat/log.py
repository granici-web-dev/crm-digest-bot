from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.chat.loop import ChatAnswer, PreviousExchange
from digest.db.schema import ChatQuestionStatus, chat_questions


@dataclass(frozen=True)
class AskedQuestion:
    chat_id: int
    user_id: int
    message_id: int
    text: str


async def questions_since(
    engine: AsyncEngine, tenant_id: str, chat_id: int, since: datetime
) -> int:
    # Отказы по лимиту в лимит не входят: иначе каждый лишний вопрос продлевал бы блокировку.
    async with engine.connect() as connection:
        result = await connection.execute(
            select(func.count()).where(
                chat_questions.c.tenant_id == tenant_id,
                chat_questions.c.chat_id == chat_id,
                chat_questions.c.created_at >= since,
                chat_questions.c.status != "rate_limited",
            )
        )
    count: int = result.scalar_one()
    return count


async def answered_question(
    engine: AsyncEngine, tenant_id: str, chat_id: int, *conditions: Any
) -> PreviousExchange | None:
    async with engine.connect() as connection:
        row = (
            await connection.execute(
                select(chat_questions.c.id, chat_questions.c.question, chat_questions.c.tool_calls)
                .where(
                    chat_questions.c.tenant_id == tenant_id,
                    chat_questions.c.chat_id == chat_id,
                    chat_questions.c.status == "answered",
                    *conditions,
                )
                .order_by(chat_questions.c.created_at.desc(), chat_questions.c.id.desc())
                .limit(1)
            )
        ).one_or_none()
    if row is None:
        return None
    calls = tuple((call["name"], call["input"]) for call in row.tool_calls if call["ok"])
    return PreviousExchange(row.id, row.question, calls) if calls else None


async def previous_exchange(
    engine: AsyncEngine,
    tenant_id: str,
    question: AskedQuestion,
    replied_bot_message_id: int | None,
    context_minutes: int,
) -> PreviousExchange | None:
    # Reply на ответ бота важнее свежести: пользователь сам указал, что уточняет.
    if replied_bot_message_id is not None:
        replied = await answered_question(
            engine,
            tenant_id,
            question.chat_id,
            chat_questions.c.reply_message_id == replied_bot_message_id,
        )
        if replied is not None:
            return replied
    # Время по часам Postgres, как пишется created_at.
    return await answered_question(
        engine,
        tenant_id,
        question.chat_id,
        chat_questions.c.user_id == question.user_id,
        chat_questions.c.created_at >= func.now() - timedelta(minutes=context_minutes),
    )


async def record_question(
    engine: AsyncEngine,
    tenant_id: str,
    question: AskedQuestion,
    status: ChatQuestionStatus,
    reply_text: str,
    duration_ms: int,
    answer: ChatAnswer | None = None,
    reply_message_id: int | None = None,
    context_question_id: int | None = None,
) -> None:
    snapshot_dates: list[date] | None = (
        None if answer is None else list(answer.snapshot_dates) or None
    )
    async with engine.begin() as connection:
        await connection.execute(
            insert(chat_questions).values(
                tenant_id=tenant_id,
                chat_id=question.chat_id,
                user_id=question.user_id,
                message_id=question.message_id,
                question=question.text,
                answer=reply_text,
                tool_calls=[]
                if answer is None
                else [
                    {
                        "name": call.name,
                        "input": call.arguments,
                        "ok": not call.outcome.is_error,
                    }
                    for call in answer.tool_calls
                ],
                snapshot_dates=snapshot_dates,
                input_tokens=None if answer is None else answer.usage.input_tokens,
                output_tokens=None if answer is None else answer.usage.output_tokens,
                cache_creation_input_tokens=(
                    None if answer is None else answer.usage.cache_creation_input_tokens
                ),
                cache_read_input_tokens=(
                    None if answer is None else answer.usage.cache_read_input_tokens
                ),
                duration_ms=duration_ms,
                status=status,
                reply_message_id=reply_message_id,
                context_question_id=context_question_id,
            )
        )
