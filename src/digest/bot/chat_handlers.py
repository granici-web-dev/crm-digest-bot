import asyncio
import logging
import re
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as clock_time

import anthropic
import pandas as pd
from aiogram import Bot, F, Router
from aiogram.types import Message, User
from anthropic import AsyncAnthropic

from digest.chat.log import AskedQuestion, previous_exchange, questions_since, record_question
from digest.chat.loop import (
    CANNOT_ANSWER_NOW_TEXT,
    QUESTION_DEADLINE_SECONDS,
    ChatAnswer,
    answer_question,
    call_label,
)
from digest.chat.state import chat_enabled
from digest.chat.tools import ToolData
from digest.config import ChatSettings
from digest.db.lead_frame import load_lead_frame, success_snapshot_dates
from digest.delivery.ops import notify_ops
from digest.reports.render import text
from digest.reports.runner import ReportDeps
from digest.snapshot import describe_error

logger = logging.getLogger(__name__)

DAILY_LIMIT_TEXT = text("daily_limit")


@dataclass(frozen=True)
class ChatDeps:
    # None: ключа ANTHROPIC_API_KEY нет, режим вопросов не включается.
    anthropic_client: AsyncAnthropic | None
    model: str
    chat_ids: frozenset[int]


def is_addressed_to_bot(message: Message, me: User, chat_settings: ChatSettings) -> bool:
    replied = message.reply_to_message
    replied_author = None if replied is None else replied.from_user
    if (
        "reply" in chat_settings.trigger
        and replied_author is not None
        and replied_author.id == me.id
    ):
        return True
    if "mention" not in chat_settings.trigger or message.text is None:
        return False
    for entity in message.entities or ():
        if entity.type == "text_mention" and entity.user is not None and entity.user.id == me.id:
            return True
        if (
            entity.type == "mention"
            and me.username is not None
            and entity.extract_from(message.text).lower() == f"@{me.username.lower()}"
        ):
            return True
    return False


def question_without_mention(text: str, me: User) -> str:
    if me.username is None:
        return text.strip()
    return re.sub(rf"@{re.escape(me.username)}\b", "", text, flags=re.IGNORECASE).strip()


def replied_bot_message_id(message: Message, me: User) -> int | None:
    replied = message.reply_to_message
    if replied is None or replied.from_user is None or replied.from_user.id != me.id:
        return None
    return replied.message_id


REJECTED_TEXT_ALERT_LIMIT = 500


def retry_outcome(answer: ChatAnswer) -> str:
    if answer.status == "answered":
        return "повтор прошёл, ответ отправлен"
    if answer.retry_timed_out:
        return "страж сработал, повтор не уложился в дедлайн, ответ не отправлен"
    if answer.retry_unverified_number is None:
        return "повтор не дал текста, ответ не отправлен"
    return f"повтор не помог (отклонено «{answer.retry_unverified_number}»), ответ не отправлен"


def rejected_texts(answer: ChatAnswer) -> str:
    # Инструменты не отдают данных клиентов, поэтому текст модели в ops допустим. Два текста
    # делят один лимит поровну, чтобы второй не выпал целиком за длинным первым.
    first = answer.rejected_text or ""
    if answer.retry_rejected_text is None:
        return first[:REJECTED_TEXT_ALERT_LIMIT]
    half = REJECTED_TEXT_ALERT_LIMIT // 2
    return f"{first[:half]} | Повтор: {answer.retry_rejected_text[:half]}"


def answer_alert(question: AskedQuestion, answer: ChatAnswer) -> str | None:
    calls = ", ".join(call_label(call) for call in answer.tool_calls) or "без инструментов"
    if answer.unverified_number is not None:
        return (
            f"Chat: страж отклонил число «{answer.unverified_number}», {retry_outcome(answer)}. "
            f"Вопрос: {question.text}. Инструменты: {calls}. Текст модели: {rejected_texts(answer)}"
        )
    if answer.status in ("max_tokens", "refusal"):
        return f"Chat: ответ модели не получен ({answer.status}). Вопрос: {question.text}."
    return None


def failure_alert(question: AskedQuestion, error: Exception) -> str:
    if isinstance(error, anthropic.APIError):
        reason = f"API Anthropic недоступен: {describe_error(error)}"
    elif isinstance(error, TimeoutError):
        reason = f"ответ не уложился в {QUESTION_DEADLINE_SECONDS} с"
    else:
        reason = f"сбой при ответе: {describe_error(error)}"
    return f"Chat: {reason}. Вопрос: {question.text}."


def elapsed_ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


async def answer_in_group(
    message: Message,
    bot: Bot,
    deps: ReportDeps,
    chat: ChatDeps,
    clock: Callable[[], datetime],
) -> None:
    if message.text is None or message.from_user is None:
        return
    me = await bot.me()
    if not is_addressed_to_bot(message, me, deps.config.modules.chat):
        return
    if chat.anthropic_client is None or not await chat_enabled(
        deps.engine, deps.tenant_id, deps.config
    ):
        return
    started = time.monotonic()
    question = AskedQuestion(
        message.chat.id,
        message.from_user.id,
        message.message_id,
        question_without_mention(message.text, me),
    )
    now = clock()
    today = now.date()
    midnight = datetime.combine(today, clock_time(), tzinfo=now.tzinfo)
    asked_today = await questions_since(deps.engine, deps.tenant_id, question.chat_id, midnight)
    if asked_today >= deps.config.modules.chat.daily_question_limit:
        sent = await message.reply(DAILY_LIMIT_TEXT)
        await record_question(
            deps.engine,
            deps.tenant_id,
            question,
            "rate_limited",
            DAILY_LIMIT_TEXT,
            elapsed_ms(started),
            reply_message_id=sent.message_id,
        )
        return

    async def load_frame(snapshot_date: date) -> pd.DataFrame:
        return await load_lead_frame(deps.engine, deps.tenant_id, snapshot_date, deps.config)

    context_question_id: int | None = None
    # Единственное место, где ловится сбой вопроса: группа получает отказ без цифр, вопрос
    # пишется в журнал и входит в дневной лимит (токены уже потрачены), ops получает алерт.
    deadline = asyncio.get_running_loop().time() + QUESTION_DEADLINE_SECONDS
    try:
        async with asyncio.timeout_at(deadline):
            previous = await previous_exchange(
                deps.engine,
                deps.tenant_id,
                question,
                replied_bot_message_id(message, me),
                deps.config.modules.chat.context_minutes,
            )
            context_question_id = None if previous is None else previous.question_id
            data = ToolData(
                today,
                await success_snapshot_dates(deps.engine, deps.tenant_id),
                load_frame,
                deps.config,
                deps.lead_links,
            )
        answer = await answer_question(
            question.text, chat.anthropic_client, chat.model, data, previous, deadline
        )
        sent = await message.reply(answer.text)
    except Exception as error:
        logger.error("chat question failed", extra={"error": describe_error(error)})
        await notify_ops(deps.ops, failure_alert(question, error))
        sent = await message.reply(CANNOT_ANSWER_NOW_TEXT)
        await record_question(
            deps.engine,
            deps.tenant_id,
            question,
            "api_error",
            CANNOT_ANSWER_NOW_TEXT,
            elapsed_ms(started),
            reply_message_id=sent.message_id,
            context_question_id=context_question_id,
        )
        return
    await record_question(
        deps.engine,
        deps.tenant_id,
        question,
        answer.status,
        answer.text,
        elapsed_ms(started),
        answer,
        reply_message_id=sent.message_id,
        context_question_id=context_question_id,
    )
    logger.info(
        "chat question answered",
        extra={
            "question": question.text,
            "context_question_id": context_question_id,
            "status": answer.status,
            "tools": [call_label(call) for call in answer.tool_calls],
            "snapshot_dates": [str(day) for day in answer.snapshot_dates],
        },
    )
    alert = answer_alert(question, answer)
    if alert is not None:
        await notify_ops(deps.ops, alert)


def chat_router(chat_ids: Collection[int]) -> Router:
    router = Router(name="chat")
    router.message.filter(F.chat.id.in_(chat_ids))
    router.message.register(answer_in_group)
    return router
