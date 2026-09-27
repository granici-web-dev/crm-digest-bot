from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import func, insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.chat.loop import ChatAnswer
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


async def record_question(
    engine: AsyncEngine,
    tenant_id: str,
    question: AskedQuestion,
    status: ChatQuestionStatus,
    reply_text: str,
    duration_ms: int,
    answer: ChatAnswer | None = None,
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
                input_tokens=None if answer is None else answer.input_tokens,
                output_tokens=None if answer is None else answer.output_tokens,
                duration_ms=duration_ms,
                status=status,
            )
        )
