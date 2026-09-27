import html
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from string import Template
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, ToolResultBlockParam
from markupsafe import Markup

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
from digest.reports.lead_links import LeadLinks
from digest.reports.render import TEMPLATES_DIR, render

MAX_TOOL_CALLS = 3
MAX_ANSWER_TOKENS = 600
PERIOD_ARGUMENTS = frozenset({"period", "period_a", "period_b"})
CANNOT_ANSWER_NOW_TEXT = "Nu pot răspunde acum, încercați mai târziu."
UNVERIFIED_NUMBERS_TEXT = (
    "Nu pot formula un răspuns exact la această întrebare. Reformulați, vă rog."
)
TOOL_LIMIT_TEXT = "Limita de apeluri pentru această întrebare a fost atinsă."
# Знак и «%» часть числа: «+15%» при результате «(−15%)» это другое число, как и счётчик 15,
# поданный как «15%». Знак только в начале слова: «top-3» это 3, а не −3.
NUMBER = re.compile(r"(?:(?<![\w.,])[+\-\u2212])?\d+(?:[.,]\d+)*(?:[ \u00a0]?%)?")
DATE_LIKE = re.compile(r"^\d{1,2}\.\d{1,2}(?:\.\d{4})?$")
MINUS_SIGNS = "-\u2212"
WORD = re.compile(r"[^\W\d_]+")
CEDILLA_TO_COMMA = str.maketrans("şţŞŢ", "șțȘȚ")
# Числительные словами промпт запрещает, словарь ловит нарушение. «un», «o», «unu», «una» это ещё
# артикли, «nouă» ещё «новая» и «нам»: в словаре они давали бы ложный отказ.
RO_NUMERALS = {
    "doi": 2,
    "două": 2,
    "trei": 3,
    "patru": 4,
    "cinci": 5,
    "șase": 6,
    "șapte": 7,
    "opt": 8,
    "zece": 10,
    "unsprezece": 11,
    "doisprezece": 12,
    "douăsprezece": 12,
    "treisprezece": 13,
    "paisprezece": 14,
    "patrusprezece": 14,
    "cincisprezece": 15,
    "șaisprezece": 16,
    "șaptesprezece": 17,
    "optsprezece": 18,
    "nouăsprezece": 19,
    "douăzeci": 20,
}


@dataclass(frozen=True)
class ExecutedToolCall:
    name: str
    arguments: dict[str, Any]
    outcome: ToolOutcome


@dataclass(frozen=True)
class PreviousExchange:
    question_id: int
    question: str
    # Успешные вызовы (имя, аргументы). Ни текста ответа, ни результатов: модель вызывает
    # инструмент заново, страж сверяет только с текущими результатами.
    tool_calls: tuple[tuple[str, dict[str, Any]], ...]


def previous_exchange_text(previous: PreviousExchange) -> str:
    return render(
        "chat_context",
        question=previous.question,
        calls=[
            f"{name} {json.dumps(arguments, ensure_ascii=False)}"
            for name, arguments in previous.tool_calls
        ],
    )


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


def system_prompt(today: date) -> str:
    # Дата явно: «25.09» и «august» без года модель превращает в дату только от сегодняшнего дня.
    template = Template((TEMPLATES_DIR / "chat_system.md").read_text(encoding="utf-8"))
    return template.substitute(today=date_label(today))


def numeral_value(word: str) -> int | None:
    return RO_NUMERALS.get(word.translate(CEDILLA_TO_COMMA).lower())


def normalized_number(token: str) -> str:
    numeral = numeral_value(token)
    if numeral is not None:
        return str(numeral)
    sign = "−" if token[0] in MINUS_SIGNS else "+" if token[0] == "+" else ""
    percent = "%" if token.endswith("%") else ""
    body = token.lstrip("+" + MINUS_SIGNS).removesuffix("%").rstrip(" \u00a0")
    # «9,6» и «9.6», «09» и «9» это одно число: модель вправе записать его любым из способов.
    parts = body.replace(",", ".").split(".")
    if len(parts) == 1 or (not percent and DATE_LIKE.match(body)):
        body = ".".join(str(int(part)) for part in parts)
    else:
        body = ".".join(parts)
    return f"{sign}{body}{percent}"


def number_tokens(text: str) -> list[str]:
    numerals = [word for word in WORD.findall(text) if numeral_value(word) is not None]
    return [*NUMBER.findall(text), *numerals]


def allowed_numbers(signature_text: str, sources: Iterable[str]) -> set[str]:
    allowed = {
        normalized_number(token)
        for source in (signature_text, *sources)
        for token in number_tokens(source)
    }
    # День, месяц и год по отдельности только из дат подписи («din 26 septembrie 2026»): их
    # видит читатель. Даты результатов и вопроса целиком, иначе любая дата разрешала бы 1–31.
    for token in NUMBER.findall(signature_text):
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
    notes = dict.fromkeys(note for call in successful for note in call.outcome.snapshot_notes)
    return render(
        "chat_signature",
        lines=[f"{call.outcome.scope} · {call_label(call)}" for call in successful],
        notes=list(notes),
    )


def outcome_text(outcome: ToolOutcome) -> str:
    return json.dumps(outcome.content, ensure_ascii=False)


async def answer_question(
    question: str,
    client: AsyncAnthropic,
    model: str,
    data: ToolData,
    previous: PreviousExchange | None = None,
) -> ChatAnswer:
    messages: list[MessageParam] = [
        {
            "role": "user",
            "content": question
            if previous is None
            else [
                {"type": "text", "text": previous_exchange_text(previous)},
                {"type": "text", "text": question},
            ],
        }
    ]
    calls: list[ExecutedToolCall] = []
    input_tokens = output_tokens = 0
    tools = tool_definitions(data.config)
    while True:
        response = await client.messages.create(
            model=model,
            max_tokens=MAX_ANSWER_TOKENS,
            system=system_prompt(data.today),
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
    return checked_answer(
        question, text, tuple(calls), input_tokens, output_tokens, data.lead_links
    )


def links_line(calls: tuple[ExecutedToolCall, ...], lead_links: LeadLinks) -> str:
    # Строку ссылок собирает код после стража цифр: номера лидов в ней страж не проверяет, а
    # модель их не видела (инвариант 7).
    lead_ids = dict.fromkeys(
        lead_id for call in calls if not call.outcome.is_error for lead_id in call.outcome.lead_ids
    )
    if not lead_ids:
        return ""
    return str(Markup("Lead-uri: ") + lead_links.capped_line(list(lead_ids)))


def checked_answer(
    question: str,
    text: str,
    calls: tuple[ExecutedToolCall, ...],
    input_tokens: int,
    output_tokens: int,
    lead_links: LeadLinks,
) -> ChatAnswer:
    # Инвариант 2 держит код: без отработавшего вызова инструмента цифр нет, с ним каждое число
    # ответа должно найтись в его результатах, в вопросе или в подписи.
    answered_calls = [call for call in calls if call.outcome.answered]
    if not answered_calls:
        if number_tokens(text):
            return ChatAnswer(
                render("chat_refusal"), "blocked_numbers", calls, input_tokens, output_tokens
            )
        if not text:
            return ChatAnswer(render("chat_refusal"), "no_tool", calls, input_tokens, output_tokens)
        return ChatAnswer(html.escape(text), "no_tool", calls, input_tokens, output_tokens)
    signature_text = signature(list(calls))
    allowed = allowed_numbers(
        signature_text, [question, *(outcome_text(call.outcome) for call in answered_calls)]
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
    footer = "\n".join(part for part in (signature_text, links_line(calls, lead_links)) if part)
    return ChatAnswer(
        f"{answer}\n\n{footer}" if footer else answer,
        "answered",
        calls,
        input_tokens,
        output_tokens,
    )
