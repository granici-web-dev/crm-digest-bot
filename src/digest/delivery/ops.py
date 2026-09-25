import logging
from dataclasses import dataclass

from aiogram import Bot

from digest.delivery.telegram import send_message_with_retry, split_message

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpsChannel:
    bot: Bot
    chat_id: int


async def notify_ops(ops: OpsChannel, text: str) -> None:
    # Алерт не должен ронять отчёт: сбой служебного бота остаётся только в логе.
    # Тексты алертов собираются из id и счётчиков, данных клиентов в них нет (инвариант 7).
    logger.warning("ops alert", extra={"alert": text})
    try:
        for part in split_message(text):
            await send_message_with_retry(ops.bot, ops.chat_id, part, parse_mode=None)
    except Exception as error:
        logger.error("ops alert not delivered", extra={"error_type": type(error).__name__})
