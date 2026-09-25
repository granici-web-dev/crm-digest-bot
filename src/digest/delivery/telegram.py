import asyncio
import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import BufferedInputFile, Message

logger = logging.getLogger(__name__)

TELEGRAM_MESSAGE_LIMIT = 4096
MAX_FLOOD_RETRIES = 3


class MessageLineTooLong(Exception):
    pass


def create_bot(token: str) -> Bot:
    return Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def telegram_length(text: str) -> int:
    # Telegram считает лимит в UTF-16 кодовых единицах: эмодзи вне BMP занимает две.
    return len(text.encode("utf-16-le")) // 2


def split_message(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    # Режем только по строкам: HTML-теги шаблонов не пересекают перевод строки,
    # поэтому каждая часть остаётся валидной разметкой.
    parts: list[str] = []
    current: list[str] = []
    current_length = 0
    for line in text.split("\n"):
        line_length = telegram_length(line)
        if line_length > limit:
            raise MessageLineTooLong(f"строка длиной {line_length} больше лимита {limit}")
        added_length = line_length if not current else line_length + 1
        if current and current_length + added_length > limit:
            parts.append("\n".join(current))
            current, current_length = [line], line_length
        else:
            current.append(line)
            current_length += added_length
    parts.append("\n".join(current))
    return parts


async def with_flood_retry(
    chat_id: int,
    send: Callable[[], Awaitable[Message]],
    sleep: Callable[[float], Awaitable[None]],
) -> int:
    for attempt in range(MAX_FLOOD_RETRIES + 1):
        try:
            message = await send()
        except TelegramRetryAfter as error:
            if attempt == MAX_FLOOD_RETRIES:
                raise
            logger.warning(
                "telegram flood wait",
                extra={"chat_id": chat_id, "retry_after": error.retry_after, "attempt": attempt},
            )
            await sleep(error.retry_after)
        else:
            return message.message_id
    raise AssertionError("unreachable")


async def send_message_with_retry(
    bot: Bot,
    chat_id: int,
    text: str,
    parse_mode: ParseMode | None = ParseMode.HTML,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    return await with_flood_retry(
        chat_id, lambda: bot.send_message(chat_id, text, parse_mode=parse_mode), sleep
    )


async def send_document_with_retry(
    bot: Bot,
    chat_id: int,
    filename: str,
    content: bytes,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> int:
    return await with_flood_retry(
        chat_id, lambda: bot.send_document(chat_id, BufferedInputFile(content, filename)), sleep
    )
