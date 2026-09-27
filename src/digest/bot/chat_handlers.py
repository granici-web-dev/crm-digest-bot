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
from digest.chat.loop import CANNOT_ANSWER_NOW_TEXT, ChatAnswer, answer_question, call_label
from digest.chat.state import chat_enabled
from digest.chat.tools import ToolData
from digest.config import ChatSettings
from digest.db.lead_frame import load_lead_frame, success_snapshot_dates
from digest.delivery.ops import notify_ops
from digest.reports.runner import ReportDeps
from digest.snapshot import describe_error

logger = logging.getLogger(__name__)

DAILY_LIMIT_TEXT = "Am atins limita de întrebări pentru azi în acest grup. Revin mâine cu plăcere."


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


def answer_alert(question: AskedQuestion, answer: ChatAnswer) -> str | None:
    calls = ", ".join(call_label(call) for call in answer.tool_calls) or "без инструментов"
    if answer.status == "unverified_numbers":
        return (
            f"Chat: ответ не отправлен, число «{answer.unverified_number}» не найдено в "
            f"результатах. Вопрос: {question.text}. Инструменты: {calls}."
        )
    if answer.status in ("max_tokens", "refusal"):
        return f"Chat: ответ модели не получен ({answer.status}). Вопрос: {question.text}."
    return None


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
    previous = await previous_exchange(
        deps.engine,
        deps.tenant_id,
        question,
        replied_bot_message_id(message, me),
        deps.config.modules.chat.context_minutes,
    )
    context_question_id = None if previous is None else previous.question_id

    async def load_frame(snapshot_date: date) -> pd.DataFrame:
        return await load_lead_frame(deps.engine, deps.tenant_id, snapshot_date, deps.config)

    data = ToolData(
        today,
        await success_snapshot_dates(deps.engine, deps.tenant_id),
        load_frame,
        deps.config,
        deps.lead_links,
    )
    try:
        answer = await answer_question(
            question.text, chat.anthropic_client, chat.model, data, previous
        )
    except anthropic.APIError as error:
        logger.error("chat question failed", extra={"error": describe_error(error)})
        await notify_ops(
            deps.ops,
            f"Chat: API Anthropic недоступен: {describe_error(error)}. Вопрос: {question.text}.",
        )
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
    sent = await message.reply(answer.text)
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
