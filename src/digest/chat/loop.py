import html
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, ToolResultBlockParam

from digest.chat.tools import (
    ALL_MANAGERS,
    ALL_SHOWROOMS,
    ToolData,
    ToolOutcome,
    date_label,
    run_tool,
    tool_definitions,
)
from digest.db.schema import ChatQuestionStatus
from digest.reports.render import TEMPLATES_DIR, render

MAX_TOOL_CALLS = 3
MAX_ANSWER_TOKENS = 600
PERIOD_ARGUMENTS = frozenset({"period", "period_a", "period_b"})
CANNOT_ANSWER_NOW_TEXT = "Nu pot răspunde acum, încercați mai târziu."
UNVERIFIED_NUMBERS_TEXT = (
    "Nu pot formula un răspuns exact la această întrebare. Reformulați, vă rog."
)
TOOL_LIMIT_TEXT = "Limita de apeluri pentru această întrebare a fost atinsă."
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
DATE_LIKE = re.compile(r"^\d{1,2}\.\d{1,2}(?:\.\d{4})?$")


@dataclass(frozen=True)
class ExecutedToolCall:
    name: str
    arguments: dict[str, Any]
    outcome: ToolOutcome


@dataclass(frozen=True)
class ChatAnswer:
    # HTML для reply: текст модели экранирован, подпись курсивом.
    text: str
    status: ChatQuestionStatus
    tool_calls: tuple[ExecutedToolCall, ...]
    input_tokens: int
    output_tokens: int
    unverified_number: str | None = None

    @property
    def snapshot_dates(self) -> tuple[date, ...]:
        return tuple(
            sorted({day for call in self.tool_calls for day in call.outcome.snapshot_dates})
        )


def system_prompt() -> str:
    return (TEMPLATES_DIR / "chat_system.md").read_text(encoding="utf-8")


def normalized_number(token: str) -> str:
    # «9,6» и «9.6», «09» и «9» это одно число: модель вправе записать его любым из способов.
    parts = token.replace(",", ".").split(".")
    if len(parts) == 1 or DATE_LIKE.match(token):
        return ".".join(str(int(part)) for part in parts)
    return ".".join(parts)


def number_tokens(text: str) -> list[str]:
    return NUMBER.findall(text)


def allowed_numbers(sources: Iterable[str]) -> set[str]:
    allowed: set[str] = set()
    for source in sources:
        for token in number_tokens(source):
            allowed.add(normalized_number(token))
            # День, месяц и год даты по отдельности: «din 26 septembrie 2026».
            if DATE_LIKE.match(token):
                allowed.update(str(int(part)) for part in token.split("."))
    return allowed


def first_unverified_number(text: str, allowed: set[str]) -> str | None:
    for token in number_tokens(text):
        if normalized_number(token) not in allowed:
            return token
    return None


def call_label(call: ExecutedToolCall) -> str:
    shown = ", ".join(
        f"{name}={value}"
        for name, value in call.arguments.items()
        if name not in PERIOD_ARGUMENTS and value not in (ALL_SHOWROOMS, ALL_MANAGERS)
    )
    return f"{call.name}({shown})"


def signature(calls: list[ExecutedToolCall]) -> str:
    successful = [call for call in calls if not call.outcome.is_error]
    as_of_dates = [
        call.outcome.data_as_of for call in successful if call.outcome.data_as_of is not None
    ]
    return render(
        "chat_signature",
        lines=[f"{call.outcome.scope} · {call_label(call)}" for call in successful],
        data_as_of=date_label(min(as_of_dates)) if as_of_dates else None,
    )


def outcome_text(outcome: ToolOutcome) -> str:
    return json.dumps(outcome.content, ensure_ascii=False)


async def answer_question(
    question: str, client: AsyncAnthropic, model: str, data: ToolData
) -> ChatAnswer:
    messages: list[MessageParam] = [{"role": "user", "content": question}]
    calls: list[ExecutedToolCall] = []
    input_tokens = output_tokens = 0
    tools = tool_definitions(data.config)
    while True:
        response = await client.messages.create(
            model=model,
            max_tokens=MAX_ANSWER_TOKENS,
            system=system_prompt(),
            tools=tools,
            # Лимит вызовов исчерпан: последний раунд только текстом.
            tool_choice={"type": "auto"} if len(calls) < MAX_TOOL_CALLS else {"type": "none"},
            # Маршрутизация по enum: рассуждение съело бы бюджет ответа в 600 токенов.
            thinking={"type": "disabled"},
            messages=messages,
        )
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens
        if response.stop_reason in ("max_tokens", "refusal"):
            return ChatAnswer(
                CANNOT_ANSWER_NOW_TEXT,
                response.stop_reason,
                tuple(calls),
                input_tokens,
                output_tokens,
            )
        if response.stop_reason != "tool_use":
            break
        messages.append({"role": "assistant", "content": response.content})
        results: list[ToolResultBlockParam] = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if len(calls) < MAX_TOOL_CALLS:
                outcome = await run_tool(block.name, block.input, data)
                calls.append(ExecutedToolCall(block.name, dict(block.input), outcome))
            else:
                outcome = ToolOutcome({"error": TOOL_LIMIT_TEXT}, is_error=True)
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": outcome_text(outcome),
                    "is_error": outcome.is_error,
                }
            )
        messages.append({"role": "user", "content": results})

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return checked_answer(question, text, tuple(calls), input_tokens, output_tokens)


def checked_answer(
    question: str,
    text: str,
    calls: tuple[ExecutedToolCall, ...],
    input_tokens: int,
    output_tokens: int,
) -> ChatAnswer:
    # Инвариант 2 держит код: без вызова инструмента цифр нет, с вызовом каждое число ответа
    # должно найтись в результатах, в вопросе или в подписи.
    if not calls:
        if number_tokens(text):
            return ChatAnswer(
                render("chat_refusal"), "blocked_numbers", calls, input_tokens, output_tokens
            )
        if not text:
            return ChatAnswer(render("chat_refusal"), "no_tool", calls, input_tokens, output_tokens)
        return ChatAnswer(html.escape(text), "no_tool", calls, input_tokens, output_tokens)
    signature_text = signature(list(calls))
    allowed = allowed_numbers(
        [question, signature_text, *(outcome_text(call.outcome) for call in calls)]
    )
    unverified = first_unverified_number(text, allowed)
    if unverified is not None:
        return ChatAnswer(
            UNVERIFIED_NUMBERS_TEXT,
            "unverified_numbers",
            calls,
            input_tokens,
            output_tokens,
            unverified,
        )
    answer = html.escape(text)
    return ChatAnswer(
        f"{answer}\n\n{signature_text}" if signature_text else answer,
        "answered",
        calls,
        input_tokens,
        output_tokens,
    )
