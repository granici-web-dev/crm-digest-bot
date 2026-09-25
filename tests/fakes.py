from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendDocument, SendMessage, TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import BufferedInputFile, Chat, Document, Message

from digest.delivery.telegram import create_bot

TEST_BOT_TOKEN = "123456:TEST"


@dataclass(frozen=True)
class SentMessage:
    chat_id: int
    text: str
    parse_mode: str | None
    message_id: int


@dataclass(frozen=True)
class SentDocument:
    chat_id: int
    filename: str
    content: bytes
    message_id: int


# Сигнатуры make_request и stream_content заданы BaseSession aiogram, timeout оттуда (ASYNC109).
class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list[SentMessage] = []
        self.documents: list[SentDocument] = []
        self.document_failures: list[Exception] = []
        self.failures: list[Exception] = []
        self.next_message_id = 1

    def fail_next(self, *errors: Exception) -> None:
        self.failures.extend(errors)

    def fail_next_document(self, *errors: Exception) -> None:
        self.document_failures.extend(errors)

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> TelegramType:
        if self.failures:
            raise self.failures.pop(0)
        if isinstance(method, SendDocument):
            if self.document_failures:
                raise self.document_failures.pop(0)
            assert isinstance(method.document, BufferedInputFile)
            chat_id = int(method.chat_id)
            message_id = self.next_message_id
            self.next_message_id += 1
            filename = method.document.filename or ""
            self.documents.append(SentDocument(chat_id, filename, method.document.data, message_id))
            document_message = Message(
                message_id=message_id,
                date=datetime.now(UTC),
                chat=Chat(id=chat_id, type="group"),
                document=Document(file_id="test", file_unique_id="test", file_name=filename),
            )
            return cast(TelegramType, document_message)
        assert isinstance(method, SendMessage)
        chat_id = int(method.chat_id)
        message_id = self.next_message_id
        self.next_message_id += 1
        parse_mode = cast(str | None, method.parse_mode)
        self.sent.append(SentMessage(chat_id, method.text, parse_mode, message_id))
        message = Message(
            message_id=message_id,
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type="group"),
            text=method.text,
        )
        return cast(TelegramType, message)

    async def close(self) -> None:
        pass

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,  # noqa: ASYNC109
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        raise NotImplementedError
        yield b""


def recording_bot() -> tuple[Bot, RecordingSession]:
    session = RecordingSession()
    bot = create_bot(TEST_BOT_TOKEN)
    bot.session = session
    return bot, session
