import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from time import monotonic
from typing import Any

from aiogram import Bot
from aiogram.client.session.middlewares.base import (
    BaseRequestMiddleware,
    NextRequestMiddlewareType,
)
from aiogram.methods import GetUpdates, Response, TelegramMethod
from aiogram.methods.base import TelegramType

from digest.delivery.ops import OpsChannel, notify_ops
from digest.snapshot import describe_error

logger = logging.getLogger(__name__)


class PollingHealth(BaseRequestMiddleware):
    # aiogram сам повторяет упавший getUpdates бесконечно и только пишет в лог:
    # время последнего успешного ответа видно лишь на уровне сессии бота.
    def __init__(self) -> None:
        self.last_get_updates_at = monotonic()

    async def __call__(
        self,
        make_request: NextRequestMiddlewareType[TelegramType],
        bot: Bot,
        method: TelegramMethod[TelegramType],
    ) -> Response[TelegramType]:
        response = await make_request(bot, method)
        if isinstance(method, GetUpdates):
            self.last_get_updates_at = monotonic()
        return response


async def stop_requested_within(stop: asyncio.Event, delay: timedelta) -> bool:
    try:
        await asyncio.wait_for(stop.wait(), delay.total_seconds())
    except TimeoutError:
        return False
    return True


async def supervise_polling(
    start_polling: Callable[[], Awaitable[Any]],
    health: PollingHealth,
    ops: OpsChannel,
    stop: asyncio.Event,
    first_retry_delay: timedelta,
    max_retry_delay: timedelta,
) -> None:
    delay = first_retry_delay
    failing = False
    while not stop.is_set():
        attempt_started_at = monotonic()
        try:
            await start_polling()
            return
        except Exception as error:
            recovered_since_last_failure = health.last_get_updates_at >= attempt_started_at
            if recovered_since_last_failure:
                delay = first_retry_delay
            logger.error(
                "polling failed",
                extra={"error": describe_error(error), "retry_in_s": delay.total_seconds()},
            )
            # Одна строка на серию сбоев: при неверном токене повторы шли бы каждые 10 минут.
            if not failing or recovered_since_last_failure:
                await notify_ops(
                    ops,
                    f"Polling бота отчётов упал: {describe_error(error)}. /settings не отвечает, "
                    f"отчёты и снапшот идут по расписанию. Повторяю запуск с паузой до "
                    f"{max_retry_delay.total_seconds() // 60:.0f} минут.",
                )
            failing = True
        if await stop_requested_within(stop, delay):
            return
        delay = min(delay * 2, max_retry_delay)


async def watch_polling_silence(
    health: PollingHealth,
    ops: OpsChannel,
    stop: asyncio.Event,
    check_interval: timedelta,
    silence_limit: timedelta,
) -> None:
    alerted = False
    while not await stop_requested_within(stop, check_interval):
        silent_for = timedelta(seconds=monotonic() - health.last_get_updates_at)
        if silent_for >= silence_limit and not alerted:
            await notify_ops(
                ops,
                f"Бот отчётов не получает апдейты Telegram {silent_for.total_seconds() // 60:.0f} "
                "минут: /settings не отвечает. Проверить токен и второй экземпляр с тем же "
                "токеном.",
            )
            alerted = True
        elif silent_for < silence_limit and alerted:
            await notify_ops(ops, "Бот отчётов снова получает апдейты Telegram.")
            alerted = False
