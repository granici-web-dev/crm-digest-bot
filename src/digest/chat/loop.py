import asyncio
import html
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date
from string import Template
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.types import MessageParam, ToolChoiceParam, ToolResultBlockParam

from digest.chat.tools import (
    ALL_MANAGERS,
    ALL_SHOWROOMS,
    ToolData,
    ToolOutcome,
    date_label,
    is_masked_name,
    run_tool,
    tool_definitions,
)
from digest.config import AppConfig
from digest.db.schema import ChatQuestionStatus
from digest.reports.lead_links import LeadLinks
from digest.reports.render import RO_MONTHS, TEMPLATES_DIR, render, text

# Таймаут клиента на каждый запрос, а вопрос это до пяти запросов с повторами и снапшоты:
# без общего дедлайна группа ждала бы ответа минутами.
QUESTION_DEADLINE_SECONDS = 60
PERIOD_ARGUMENTS = frozenset({"period", "period_a", "period_b"})
CANNOT_ANSWER_NOW_TEXT = text("cannot_answer_now")
UNVERIFIED_NUMBERS_TEXT = text("unverified_numbers")
# Знак и «%» часть числа: «+15%» при результате «(−15%)» это другое число, как и счётчик 15,
# поданный как «15%». Знак только в начале слова: «top-3» это 3, а не −3.
NUMBER = re.compile(r"(?:(?<![\w.,])[+\-\u2212])?\d+(?:[.,]\d+)*(?:[ \u00a0]?%)?")
DATE_LIKE = re.compile(r"^\d{1,2}\.\d{1,2}(?:\.\d{4})?$")
DAY_MONTH = re.compile(
    rf"(?<![\w.,])(\d{{1,2}}) ({'|'.join(RO_MONTHS)})(?: (\d{{4}}))?(?!\w)", re.IGNORECASE
)
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
    text: str
    status: ChatQuestionStatus
    tool_calls: tuple[ExecutedToolCall, ...]
    input_tokens: int
    output_tokens: int
    # Число и текст модели, отклонённые стражем в первой попытке: для алерта в ops и вывода
    # eval, в базу не пишутся. У answered они значат, что прошла повторная попытка.
    unverified_number: str | None = None
    rejected_text: str | None = None
    retried: bool = False
    # Провал повтора: его число и текст (None, если модель не дала текста) или истёкший дедлайн.
    retry_unverified_number: str | None = None
    retry_rejected_text: str | None = None
    retry_timed_out: bool = False

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


def config_mask_names(config: AppConfig) -> frozenset[str]:
    status_mapping = config.status_mapping
    return frozenset(
        {
            *status_mapping.category_by_status,
            *(source for group in status_mapping.sources.model_dump().values() for source in group),
            *status_mapping.showrooms,
            *(manager.name for manager in config.managers.managers),
        }
    )


def name_mask_pattern(names: Iterable[str]) -> re.Pattern[str]:
    # Цифры в именах mefi («Revenire 2», «BIFE 2026») и подписях кампаний («Promo 30») это часть
    # имени, а не число: без маски имя в результате разрешило бы голую «2» во всём ответе, а имя
    # в ответе давало отказ. Регистр учитывается (имена mefi не нормализуются), а после «Data
    # revenire» идёт дата или число дней, не статус: иначе «Data Revenire 3 octombrie» прятала бы
    # от стража «3».
    masked = sorted((name for name in names if is_masked_name(name)), key=len, reverse=True)
    alternatives = [rf"(?<!(?i:data)\s)(?<!\w){re.escape(name)}(?!\w)" for name in masked]
    return re.compile("|".join(alternatives) or "(?!)")


def date_form(day: str, month: str, year: str | None = None) -> str | None:
    # Отдельная форма даты: иначе разрешённая «28.09» разрешила бы и дробь «28,9».
    if not (1 <= int(day) <= 31 and 1 <= int(month) <= 12):
        return None
    return f"date:{int(day)}.{int(month)}" + ("" if year is None else f".{int(year)}")


def numeric_date_form(token: str) -> str | None:
    if not DATE_LIKE.match(token):
        return None
    day, month, *year = token.split(".")
    return date_form(day, month, *year)


def allowed_date_forms(token: str) -> set[str]:
    # Полная дата результата разрешает и свою короткую форму, короткая полную не разрешает.
    full = numeric_date_form(token)
    if full is None:
        return set()
    day, month = token.split(".")[:2]
    return {full, *filter(None, [date_form(day, month)])}


def day_month_form(match: re.Match[str]) -> str | None:
    day, month_name, year = match.groups()
    return date_form(day, str(RO_MONTHS.index(month_name.lower()) + 1), year)


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
    # Дата целиком («28.09», «28 septembrie») разрешена, если она есть в результате или подписи.
    allowed.update(
        form
        for source in (signature_text, *sources)
        for token in NUMBER.findall(source)
        for form in allowed_date_forms(token)
    )
    return allowed


def first_unverified_number(text: str, allowed: set[str]) -> str | None:
    without_known_dates = DAY_MONTH.sub(
        lambda match: " " if day_month_form(match) in allowed else match.group(0), text
    )
    for token in number_tokens(without_known_dates):
        if normalized_number(token) in allowed or numeric_date_form(token) in allowed:
            continue
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
    previous: PreviousExchange | None,
    deadline: float,
) -> ChatAnswer:
    # deadline во времени цикла событий (loop.time()). Первая попытка и повтор под отдельными
    # таймаутами: истечение на повторе даёт отказ стража, а не сбой вопроса.
    async with asyncio.timeout_at(deadline):
        answer, messages = await first_attempt(question, client, model, data, previous)
    # Число отклонения есть только у unverified_numbers.
    if answer.unverified_number is None:
        return answer
    try:
        async with asyncio.timeout_at(deadline):
            return await guard_retry(
                question, answer, answer.unverified_number, messages, client, model, data
            )
    except TimeoutError:
        return replace(answer, retried=True, retry_timed_out=True)


def model_request(
    model: str, data: ToolData, messages: list[MessageParam], tool_choice: ToolChoiceParam
) -> dict[str, Any]:
    return {
        "model": model,
        "max_tokens": data.config.modules.chat.max_answer_tokens,
        "system": system_prompt(data.today),
        "tools": tool_definitions(data.config),
        "tool_choice": tool_choice,
        # Маршрутизация по enum: рассуждение съело бы бюджет короткого ответа.
        "thinking": {"type": "disabled"},
        "messages": messages,
    }


async def first_attempt(
    question: str,
    client: AsyncAnthropic,
    model: str,
    data: ToolData,
    previous: PreviousExchange | None,
) -> tuple[ChatAnswer, list[MessageParam]]:
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
    max_tool_calls = data.config.modules.chat.max_tool_calls
    while True:
        response = await client.messages.create(
            **model_request(
                model,
                data,
                messages,
                {"type": "auto"} if len(calls) < max_tool_calls else {"type": "none"},
            )
        )
        input_tokens += response.usage.input_tokens
        output_tokens += response.usage.output_tokens
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason in ("max_tokens", "refusal"):
            failed = ChatAnswer(
                CANNOT_ANSWER_NOW_TEXT,
                response.stop_reason,
                tuple(calls),
                input_tokens,
                output_tokens,
            )
            return failed, messages
        if response.stop_reason != "tool_use":
            break
        results: list[ToolResultBlockParam] = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if len(calls) < max_tool_calls:
                outcome = await run_tool(block.name, block.input, data)
                calls.append(ExecutedToolCall(block.name, dict(block.input), outcome))
            else:
                outcome = ToolOutcome({"error": text("tool_call_limit")}, is_error=True)
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": outcome_text(outcome),
                    "is_error": outcome.is_error,
                }
            )
        messages.append({"role": "user", "content": results})
    answer = checked_answer(
        question,
        response_text(response.content),
        tuple(calls),
        input_tokens,
        output_tokens,
        data.lead_links,
        config_mask_names(data.config),
    )
    return answer, messages


async def guard_retry(
    question: str,
    rejected: ChatAnswer,
    rejected_number: str,
    messages: list[MessageParam],
    client: AsyncAnthropic,
    model: str,
    data: ToolData,
) -> ChatAnswer:
    # Одна просьба переписать без отклонённого числа (shape chat-derived-numbers, «Согласовано»):
    # те же результаты в истории, новых вызовов нет.
    retry_messages: list[MessageParam] = [
        *messages,
        {"role": "user", "content": text("guard_retry", number=rejected_number)},
    ]
    response = await client.messages.create(
        **model_request(model, data, retry_messages, {"type": "none"})
    )
    input_tokens = rejected.input_tokens + response.usage.input_tokens
    output_tokens = rejected.output_tokens + response.usage.output_tokens
    retried = replace(
        rejected, input_tokens=input_tokens, output_tokens=output_tokens, retried=True
    )
    if response.stop_reason != "end_turn":
        return retried
    second = checked_answer(
        question,
        response_text(response.content),
        rejected.tool_calls,
        input_tokens,
        output_tokens,
        data.lead_links,
        config_mask_names(data.config),
    )
    if second.status == "answered":
        return replace(
            second,
            unverified_number=rejected_number,
            rejected_text=rejected.rejected_text,
            retried=True,
        )
    return replace(
        retried,
        retry_unverified_number=second.unverified_number,
        retry_rejected_text=second.rejected_text,
    )


def response_text(content: Iterable[Any]) -> str:
    return "".join(block.text for block in content if block.type == "text").strip()


def links_line(calls: tuple[ExecutedToolCall, ...], lead_links: LeadLinks) -> str:
    # Строку ссылок собирает код после стража цифр: номера лидов в ней страж не проверяет, а
    # модель их не видела (инвариант 7).
    lead_ids = dict.fromkeys(
        lead_id for call in calls if not call.outcome.is_error for lead_id in call.outcome.lead_ids
    )
    if not lead_ids:
        return ""
    return str(text("links_line", links=lead_links.capped_line(list(lead_ids))))


def checked_answer(
    question: str,
    model_text: str,
    calls: tuple[ExecutedToolCall, ...],
    input_tokens: int,
    output_tokens: int,
    lead_links: LeadLinks,
    config_names: frozenset[str],
) -> ChatAnswer:
    # Инвариант 2 держит код: без отработавшего вызова инструмента цифр нет, с ним каждое число
    # ответа должно найтись в его результатах, в вопросе или в подписи.
    answered_calls = [call for call in calls if call.outcome.answered]
    if not answered_calls:
        if number_tokens(model_text):
            return ChatAnswer(
                render("chat_refusal"), "blocked_numbers", calls, input_tokens, output_tokens
            )
        if not model_text:
            return ChatAnswer(render("chat_refusal"), "no_tool", calls, input_tokens, output_tokens)
        return ChatAnswer(html.escape(model_text), "no_tool", calls, input_tokens, output_tokens)
    signature_text = signature(list(calls))
    masked_name_pattern = name_mask_pattern(
        config_names.union(*(call.outcome.masked_names for call in answered_calls))
    )
    allowed = allowed_numbers(
        masked_name_pattern.sub(" ", signature_text),
        [
            masked_name_pattern.sub(" ", source)
            for source in (question, *(outcome_text(call.outcome) for call in answered_calls))
        ],
    )
    unverified = first_unverified_number(masked_name_pattern.sub(" ", model_text), allowed)
    if unverified is not None:
        return ChatAnswer(
            UNVERIFIED_NUMBERS_TEXT,
            "unverified_numbers",
            calls,
            input_tokens,
            output_tokens,
            unverified,
            model_text,
        )
    answer = html.escape(model_text)
    footer = "\n".join(part for part in (signature_text, links_line(calls, lead_links)) if part)
    return ChatAnswer(
        f"{answer}\n\n{footer}" if footer else answer,
        "answered",
        calls,
        input_tokens,
        output_tokens,
    )
