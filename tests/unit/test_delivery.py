import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import SendMessage

from digest.delivery.ops import OpsChannel, notify_ops
from digest.delivery.telegram import (
    MAX_FLOOD_RETRIES,
    send_document_with_retry,
    send_message_with_retry,
)
from fakes import recording_bot

OPS_CHAT_ID = -100


def flood_wait(seconds: int) -> TelegramRetryAfter:
    return TelegramRetryAfter(SendMessage(chat_id=1, text="x"), "flood", seconds)


async def test_flood_wait_is_waited_out_and_message_resent() -> None:
    bot, session = recording_bot()
    session.fail_next(flood_wait(7))
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    message_id = await send_message_with_retry(bot, 42, "text", sleep=record_sleep)

    assert sleeps == [7]
    assert [(sent.chat_id, sent.message_id) for sent in session.sent] == [(42, message_id)]


async def test_flood_wait_beyond_retry_budget_raises() -> None:
    bot, session = recording_bot()
    session.fail_next(*(flood_wait(1) for _ in range(MAX_FLOOD_RETRIES + 1)))

    async def no_sleep(seconds: float) -> None:
        pass

    with pytest.raises(TelegramRetryAfter):
        await send_message_with_retry(bot, 42, "text", sleep=no_sleep)
    assert session.sent == []


async def test_document_is_sent_with_filename_after_flood_wait() -> None:
    bot, session = recording_bot()
    session.fail_next(flood_wait(3))
    sleeps: list[float] = []

    async def record_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    message_id = await send_document_with_retry(
        bot, 42, "sofabelle_sapt39_2026.xlsx", b"xlsx", sleep=record_sleep
    )

    assert sleeps == [3]
    [document] = session.documents
    assert (document.chat_id, document.filename, document.content, document.message_id) == (
        42,
        "sofabelle_sapt39_2026.xlsx",
        b"xlsx",
        message_id,
    )


async def test_ops_alert_goes_to_ops_chat_as_plain_text() -> None:
    bot, session = recording_bot()

    await notify_ops(OpsChannel(bot, OPS_CHAT_ID), "UNMAPPED: 3 <lead>")

    assert [(sent.chat_id, sent.text, sent.parse_mode) for sent in session.sent] == [
        (OPS_CHAT_ID, "UNMAPPED: 3 <lead>", None)
    ]


async def test_ops_bot_failure_does_not_propagate() -> None:
    bot, session = recording_bot()
    session.fail_next(TelegramNetworkError(SendMessage(chat_id=1, text="x"), "down"))

    await notify_ops(OpsChannel(bot, OPS_CHAT_ID), "snapshot failed")

    assert session.sent == []
