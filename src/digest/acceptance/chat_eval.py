import re
import statistics
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, Self

import pandas as pd
import yaml
from anthropic import AsyncAnthropic
from pydantic import BaseModel, ConfigDict, TypeAdapter, model_validator

from digest.chat.loop import (
    ChatAnswer,
    ExecutedToolCall,
    PreviousExchange,
    answer_question,
    call_label,
    normalized_number,
    number_tokens,
    signature,
)
from digest.chat.tools import FrameLoader, ToolData, ToolOutcome, run_tool
from digest.config import ChatToolName
from digest.snapshot import describe_error

Expectation = Literal["answer", "refusal", "no_data"]
ToolArgumentsSet = dict[str, Any]

# Ворота M6, docs/success-criteria.md, раздел «1. MVP».
PASS_THRESHOLD = 27
MEDIAN_SECONDS_THRESHOLD = 15.0
CONTACTS_TAG = "contacts"
GUARD_STATUSES = frozenset({"unverified_numbers", "blocked_numbers"})
# Цена за 1M токенов (вход, выход), USD. Для модели вне таблицы стоимость не печатается.
PRICES_PER_MILLION_TOKENS: dict[str, tuple[float, float]] = {
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-5-5": (2.0, 10.0),
}
CURRENCY_AMOUNT = re.compile(
    r"\d[\d.,\s ]*\s*(?:lei|ron|€|eur)\b|(?:€|eur|ron)\s*\d", re.IGNORECASE
)
PATH_PART = re.compile(r"^(\w+)(?:\[(\w+)=([^\]]+)\])?$")


class ExpectedCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: ChatToolName
    arguments: ToolArgumentsSet | list[ToolArgumentsSet]
    numbers: list[str] = []

    @property
    def argument_options(self) -> list[ToolArgumentsSet]:
        return self.arguments if isinstance(self.arguments, list) else [self.arguments]


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    tags: list[str]
    question: str
    expect: Expectation
    after: str | None = None
    calls: list[ExpectedCall] = []

    @model_validator(mode="after")
    def calls_match_expectation(self) -> Self:
        if self.expect == "answer" and not self.calls:
            raise ValueError(f"{self.id}: expect answer без calls")
        if self.expect == "refusal" and self.calls:
            raise ValueError(f"{self.id}: expect refusal не ждёт вызовов")
        return self


GOLDEN_CASES = TypeAdapter(list[GoldenCase])


def load_golden_cases(path: Path) -> list[GoldenCase]:
    cases = GOLDEN_CASES.validate_python(yaml.safe_load(path.read_text(encoding="utf-8")))
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise ValueError(f"повтор id {case.id}")
        if case.after is not None and case.after not in seen:
            raise ValueError(f"{case.id}: after {case.after} не встречался выше")
        seen.add(case.id)
    return cases


def with_dependencies(cases: list[GoldenCase], only: set[str]) -> list[GoldenCase]:
    by_id = {case.id: case for case in cases}
    selected: set[str] = set()
    for case_id in only:
        current: str | None = case_id
        while current is not None and current not in selected:
            selected.add(current)
            current = by_id[current].after
    return [case for case in cases if case.id in selected]


def matching_call(
    expected: ExpectedCall, calls: Sequence[ExecutedToolCall]
) -> ExecutedToolCall | None:
    for call in calls:
        if call.name == expected.tool and call.arguments in expected.argument_options:
            return call
    return None


def value_at(content: dict[str, Any], path: str) -> Any:
    value: Any = content
    for part in path.split("."):
        match = PATH_PART.match(part)
        if match is None:
            raise ValueError(f"путь {path}: не разобрать «{part}»")
        key, filter_key, filter_value = match.groups()
        value = value[key]
        if filter_key is not None:
            [value] = [item for item in value if str(item[filter_key]) == filter_value]
    return value


def unsigned(token: str) -> str:
    return token.lstrip("+−")


def required_tokens(value: Any) -> list[str]:
    if value is None or value == "—":
        return []
    if isinstance(value, bool):
        raise ValueError("булево значение не число ответа")
    tokens = number_tokens(str(value))
    # Фраза изменения («… față de …: +17,4%») содержит даты периодов: требуем только процент.
    percents = [token for token in tokens if token.endswith("%")]
    return [unsigned(normalized_number(token)) for token in (percents or tokens)]


def model_text(answer: ChatAnswer) -> str:
    # Подпись и ссылки добавляет код: их числа (даты, #id) не должны засчитываться модели.
    footer = signature(list(answer.tool_calls))
    index = answer.text.rfind("\n\n" + footer) if footer else -1
    return answer.text if index < 0 else answer.text[:index]


@dataclass(frozen=True)
class Grade:
    passed: bool
    reason: str


def grade_case(
    case: GoldenCase, answer: ChatAnswer, references: Sequence[ToolOutcome | None]
) -> Grade:
    text = model_text(answer)
    if case.expect == "refusal":
        if answer.tool_calls:
            called = ", ".join(call_label(call) for call in answer.tool_calls)
            return Grade(False, f"вызван инструмент при отказе: {called}")
        if answer.status != "no_tool" or number_tokens(text):
            return Grade(False, f"отказ с цифрами, статус {answer.status}")
        return Grade(True, "")
    if case.expect == "no_data":
        if answer.status in GUARD_STATUSES:
            return Grade(False, f"статус {answer.status}")
        if CURRENCY_AMOUNT.search(text):
            return Grade(False, "сумма в валюте в ответе")
        return Grade(True, "")

    for expected, reference in zip(case.calls, references, strict=True):
        if reference is not None and reference.no_data:
            return Grade(False, f"вопрос устарел, поменять дату ({expected.tool})")
        if reference is not None and reference.is_error:
            return Grade(False, f"эталонный вызов {expected.tool} с ошибкой: {reference.content}")
    if answer.status != "answered":
        return Grade(False, f"статус {answer.status}")
    answer_numbers = {unsigned(normalized_number(token)) for token in number_tokens(text)}
    for expected, reference in zip(case.calls, references, strict=True):
        if matching_call(expected, answer.tool_calls) is None or reference is None:
            called = ", ".join(call_label(call) for call in answer.tool_calls) or "нет вызовов"
            return Grade(False, f"нет вызова {expected.tool} с ожидаемыми аргументами ({called})")
        for path in expected.numbers:
            for token in required_tokens(value_at(reference.content, path)):
                if token not in answer_numbers:
                    return Grade(False, f"нет числа {path}={token}")
    return Grade(True, "")


@dataclass(frozen=True)
class CaseResult:
    case: GoldenCase
    grade: Grade
    seconds: float
    answer: ChatAnswer | None

    @property
    def calls_label(self) -> str:
        if self.answer is None:
            return ""
        return "; ".join(call_label(call) for call in self.answer.tool_calls)


async def reference_outcomes(
    case: GoldenCase, answer: ChatAnswer, data: ToolData
) -> list[ToolOutcome | None]:
    references: list[ToolOutcome | None] = []
    for expected in case.calls:
        matched = matching_call(expected, answer.tool_calls)
        arguments = matched.arguments if matched is not None else expected.argument_options[0]
        # Числа эталона считает тот же инструмент на том же снапшоте, а не хардкод в YAML.
        references.append(await run_tool(expected.tool, arguments, data))
    return references


async def run_chat_eval(
    cases: list[GoldenCase], client: AsyncAnthropic, model: str, data: ToolData
) -> list[CaseResult]:
    results: list[CaseResult] = []
    answers: dict[str, ChatAnswer] = {}
    questions = {case.id: case.question for case in cases}
    for case in cases:
        previous_answer = None if case.after is None else answers.get(case.after)
        # Как previous_exchange в digest/chat/log.py: вопрос и успешные вызовы, без текста ответа.
        previous = (
            None
            if previous_answer is None or case.after is None
            else PreviousExchange(
                0,
                questions[case.after],
                tuple(
                    (call.name, call.arguments)
                    for call in previous_answer.tool_calls
                    if not call.outcome.is_error
                ),
            )
        )
        started = time.monotonic()
        try:
            answer = await answer_question(case.question, client, model, data, previous)
        except Exception as error:
            grade = Grade(False, f"ошибка API: {describe_error(error)}")
            results.append(CaseResult(case, grade, time.monotonic() - started, None))
            continue
        seconds = time.monotonic() - started
        answers[case.id] = answer
        references = await reference_outcomes(case, answer, data) if case.expect == "answer" else []
        results.append(CaseResult(case, grade_case(case, answer, references), seconds, answer))
    return results


def memoized_frame_loader(load_frame: FrameLoader) -> FrameLoader:
    frames: dict[date, pd.DataFrame] = {}

    async def load(snapshot_date: date) -> pd.DataFrame:
        if snapshot_date not in frames:
            frames[snapshot_date] = await load_frame(snapshot_date)
        return frames[snapshot_date]

    return load


@dataclass(frozen=True)
class EvalSummary:
    passed: int
    total: int
    contacts_passed: int
    contacts_total: int
    guard_hits: int
    median_seconds: float
    input_tokens: int
    output_tokens: int
    cost_usd: float | None

    @property
    def meets_gate(self) -> bool:
        return (
            self.passed >= PASS_THRESHOLD
            and self.guard_hits == 0
            and self.contacts_passed == self.contacts_total
            and self.median_seconds <= MEDIAN_SECONDS_THRESHOLD
        )


def summarize(results: list[CaseResult], model: str) -> EvalSummary:
    answers = [result.answer for result in results if result.answer is not None]
    contacts = [result for result in results if CONTACTS_TAG in result.case.tags]
    input_tokens = sum(answer.input_tokens for answer in answers)
    output_tokens = sum(answer.output_tokens for answer in answers)
    prices = PRICES_PER_MILLION_TOKENS.get(model)
    return EvalSummary(
        passed=sum(result.grade.passed for result in results),
        total=len(results),
        contacts_passed=sum(result.grade.passed for result in contacts),
        contacts_total=len(contacts),
        guard_hits=sum(answer.status in GUARD_STATUSES for answer in answers),
        median_seconds=statistics.median(result.seconds for result in results),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=(
            None
            if prices is None
            else (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000
        ),
    )


def failures_by_tag(results: list[CaseResult]) -> Counter[str]:
    return Counter(tag for result in results if not result.grade.passed for tag in result.case.tags)


def eval_table_lines(results: list[CaseResult], summary: EvalSummary) -> list[str]:
    lines = [
        "| id | вопрос | итог | причина | время, с | вызовы |",
        "|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {result.case.id} | {result.case.question} | "
        f"{'ok' if result.grade.passed else 'FAIL'} | {result.grade.reason} | "
        f"{result.seconds:.1f} | {result.calls_label} |"
        for result in results
    ]
    cost = "—" if summary.cost_usd is None else f"≈ ${summary.cost_usd:.2f}"
    by_tag = ", ".join(f"{tag} {count}" for tag, count in failures_by_tag(results).most_common())
    lines += [
        f"Итог {summary.passed}/{summary.total}",
        f"Отказы на контакты {summary.contacts_passed}/{summary.contacts_total}",
        f"Числа не из инструментов {summary.guard_hits}",
        f"Медиана {summary.median_seconds:.1f} с",
        f"Токены in/out {summary.input_tokens}/{summary.output_tokens}, {cost}",
        f"Провалы по тегам: {by_tag or 'нет'}",
        f"Ворота M6: {'пройдены' if summary.meets_gate else 'не пройдены'}",
    ]
    return lines


def answer_text_lines(results: list[CaseResult]) -> list[str]:
    lines = ["", "Ответы (только в терминал):"]
    for result in results:
        text = "—" if result.answer is None else result.answer.text
        lines += [f"--- {result.case.id} [{result.grade.reason or 'ok'}]", text]
    return lines
