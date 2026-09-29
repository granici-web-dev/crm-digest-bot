import asyncio
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import httpx2
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import (
    AnswerCallbackQuery,
    EditMessageText,
    GetMe,
    SendDocument,
    SendMessage,
    SendPhoto,
    TelegramMethod,
)
from aiogram.methods.base import TelegramType
from aiogram.types import (
    BufferedInputFile,
    Chat,
    Document,
    InlineKeyboardMarkup,
    Message,
    PhotoSize,
    User,
)
from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

from digest.delivery.telegram import create_bot

TEST_BOT_TOKEN = "123456:TEST"
BOT_USER = User(id=123456, is_bot=True, first_name="Sofabelle", username="sofabelle_digest_bot")


@dataclass(frozen=True)
class SentMessage:
    chat_id: int
    text: str
    parse_mode: str | None
    message_id: int
    reply_markup: InlineKeyboardMarkup | None = None
    reply_to_message_id: int | None = None


@dataclass(frozen=True)
class EditedMessage:
    chat_id: int
    message_id: int
    text: str
    reply_markup: InlineKeyboardMarkup | None


@dataclass(frozen=True)
class CallbackAnswer:
    callback_query_id: str
    text: str | None
    show_alert: bool | None


@dataclass(frozen=True)
class SentDocument:
    chat_id: int
    filename: str
    content: bytes
    message_id: int


@dataclass(frozen=True)
class SentPhoto:
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
        self.photos: list[SentPhoto] = []
        self.edited: list[EditedMessage] = []
        self.callback_answers: list[CallbackAnswer] = []
        self.photo_failures: list[Exception] = []
        self.document_failures: list[Exception] = []
        self.failures: list[Exception] = []
        self.next_message_id = 1

    def fail_next(self, *errors: Exception) -> None:
        self.failures.extend(errors)

    def fail_next_document(self, *errors: Exception) -> None:
        self.document_failures.extend(errors)

    def fail_next_photo(self, *errors: Exception) -> None:
        self.photo_failures.extend(errors)

    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> TelegramType:
        if self.failures:
            raise self.failures.pop(0)
        if isinstance(method, GetMe):
            return cast(TelegramType, BOT_USER)
        if isinstance(method, AnswerCallbackQuery):
            self.callback_answers.append(
                CallbackAnswer(method.callback_query_id, method.text, method.show_alert)
            )
            return cast(TelegramType, True)
        if isinstance(method, EditMessageText):
            assert method.chat_id is not None
            assert method.message_id is not None
            assert method.text is not None
            chat_id = int(method.chat_id)
            self.edited.append(
                EditedMessage(chat_id, method.message_id, method.text, method.reply_markup)
            )
            edited_message = Message(
                message_id=method.message_id,
                date=datetime.now(UTC),
                chat=Chat(id=chat_id, type="private"),
                text=method.text,
            )
            return cast(TelegramType, edited_message)
        if isinstance(method, SendPhoto):
            if self.photo_failures:
                raise self.photo_failures.pop(0)
            assert isinstance(method.photo, BufferedInputFile)
            chat_id = int(method.chat_id)
            message_id = self.next_message_id
            self.next_message_id += 1
            filename = method.photo.filename or ""
            self.photos.append(SentPhoto(chat_id, filename, method.photo.data, message_id))
            photo_message = Message(
                message_id=message_id,
                date=datetime.now(UTC),
                chat=Chat(id=chat_id, type="group"),
                photo=[PhotoSize(file_id="test", file_unique_id="test", width=1, height=1)],
            )
            return cast(TelegramType, photo_message)
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
        reply_markup = cast(InlineKeyboardMarkup | None, method.reply_markup)
        reply_to = None if method.reply_parameters is None else method.reply_parameters.message_id
        self.sent.append(
            SentMessage(chat_id, method.text, parse_mode, message_id, reply_markup, reply_to)
        )
        message = Message(
            message_id=message_id,
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type="group"),
            text=method.text,
        )
        return cast(TelegramType, message)

    @property
    def request_count(self) -> int:
        return (
            len(self.sent)
            + len(self.documents)
            + len(self.photos)
            + len(self.edited)
            + len(self.callback_answers)
        )

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


@dataclass
class ScriptedAnthropic:
    # Настоящий AsyncAnthropic поверх MockTransport: ответы API заданы заранее, запросы записаны.
    client: AsyncAnthropic
    requests: list[dict[str, Any]]


def scripted_anthropic(
    *responses: dict[str, Any] | int, delays: tuple[float, ...] = ()
) -> ScriptedAnthropic:
    # delays[i]: пауза в секундах перед i-м ответом, для проверки дедлайнов.
    remaining = list(responses)
    requests: list[dict[str, Any]] = []

    async def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(json.loads(request.content))
        index = len(requests) - 1
        if index < len(delays):
            await asyncio.sleep(delays[index])
        response = remaining.pop(0)
        if isinstance(response, int):
            return httpx2.Response(
                response,
                json={"type": "error", "error": {"type": "api_error", "message": "scripted"}},
            )
        return httpx2.Response(200, json=response)

    client = AsyncAnthropic(
        api_key="test",
        max_retries=0,
        http_client=DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handle)),
    )
    return ScriptedAnthropic(client, requests)


def anthropic_message(
    content: list[dict[str, Any]],
    stop_reason: str,
    input_tokens: int = 100,
    output_tokens: int = 20,
) -> dict[str, Any]:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def tool_use_message(*calls: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    return anthropic_message(
        [
            {"type": "tool_use", "id": f"toolu_{index}", "name": name, "input": arguments}
            for index, (name, arguments) in enumerate(calls)
        ],
        "tool_use",
    )


def text_message(text: str, stop_reason: str = "end_turn") -> dict[str, Any]:
    return anthropic_message([{"type": "text", "text": text}], stop_reason)
