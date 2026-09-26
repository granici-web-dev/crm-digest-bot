import asyncio
from collections.abc import Callable
from datetime import timedelta
from time import monotonic
from typing import Any

import pytest
from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe, GetUpdates, SendMessage, TelegramMethod
from aiogram.types import User

from digest.bot.polling import PollingHealth, supervise_polling, watch_polling_silence
from digest.delivery.ops import OpsChannel
from fakes import RecordingSession, recording_bot

OPS_CHAT_ID = -1003
TINY = timedelta(milliseconds=1)


def network_error() -> TelegramNetworkError:
    return TelegramNetworkError(method=GetMe(), message="connection refused")


def ops_channel() -> tuple[OpsChannel, RecordingSession]:
    ops_bot, ops_session = recording_bot()
    return OpsChannel(ops_bot, OPS_CHAT_ID), ops_session


class ScriptedPolling:
    def __init__(self, steps: list[Callable[[], None]], stop: asyncio.Event) -> None:
        self.steps = steps
        self.stop = stop
        self.attempts = 0

    async def __call__(self) -> None:
        self.attempts += 1
        self.steps.pop(0)()
        if not self.steps:
            self.stop.set()


# Проверка каждую миллисекунду: за 50 мс наблюдатель успевает десятки раз.
WATCH_PERIOD_S = 0.05


def raise_network_error() -> None:
    raise network_error()


async def test_polling_failure_alerts_once_and_retries_until_polling_runs() -> None:
    ops, ops_session = ops_channel()
    stop = asyncio.Event()
    health = PollingHealth()
    start_polling = ScriptedPolling([raise_network_error, raise_network_error, lambda: None], stop)

    await supervise_polling(start_polling, health, ops, stop, TINY, TINY * 4)

    assert start_polling.attempts == 3
    [alert] = [message.text for message in ops_session.sent]
    assert alert.startswith("Polling бота отчётов упал: TelegramNetworkError")
    assert "отчёты и снапшот идут по расписанию" in alert


async def test_polling_failure_after_recovery_alerts_again() -> None:
    ops, ops_session = ops_channel()
    stop = asyncio.Event()
    health = PollingHealth()

    def get_updates_worked_then_failed() -> None:
        health.last_get_updates_at = monotonic()
        raise network_error()

    start_polling = ScriptedPolling(
        [raise_network_error, get_updates_worked_then_failed, lambda: None], stop
    )

    await supervise_polling(start_polling, health, ops, stop, TINY, TINY * 4)

    assert len(ops_session.sent) == 2


async def test_stop_during_retry_pause_ends_supervision() -> None:
    ops, _ = ops_channel()
    stop = asyncio.Event()
    attempts = 0

    async def always_failing() -> None:
        nonlocal attempts
        attempts += 1
        stop.set()
        raise network_error()

    await supervise_polling(
        always_failing, PollingHealth(), ops, stop, timedelta(minutes=10), timedelta(minutes=10)
    )

    assert attempts == 1


async def test_long_silence_alerts_once_and_recovery_is_reported() -> None:
    ops, ops_session = ops_channel()
    stop = asyncio.Event()
    health = PollingHealth()
    health.last_get_updates_at = monotonic() - 31 * 60
    watcher = asyncio.create_task(
        watch_polling_silence(health, ops, stop, TINY, timedelta(minutes=30))
    )

    await asyncio.sleep(WATCH_PERIOD_S)
    assert len(ops_session.sent) == 1
    assert ops_session.sent[0].text.startswith("Бот отчётов не получает апдейты Telegram 31 минут")

    health.last_get_updates_at = monotonic()
    await asyncio.sleep(WATCH_PERIOD_S)
    stop.set()
    await watcher

    assert len(ops_session.sent) == 2
    assert ops_session.sent[1].text == "Бот отчётов снова получает апдейты Telegram."


async def test_health_tracks_only_successful_get_updates() -> None:
    health = PollingHealth()
    health.last_get_updates_at = 0.0
    bot, _ = recording_bot()

    async def succeed(bot: Bot, method: TelegramMethod[Any]) -> Any:
        return User(id=1, is_bot=True, first_name="bot")

    async def fail(bot: Bot, method: TelegramMethod[Any]) -> Any:
        raise network_error()

    await health(succeed, bot, SendMessage(chat_id=1, text="x"))
    assert health.last_get_updates_at == 0.0

    with pytest.raises(TelegramNetworkError):
        await health(fail, bot, GetUpdates())
    assert health.last_get_updates_at == 0.0

    await health(succeed, bot, GetUpdates())
    assert health.last_get_updates_at > 0.0
