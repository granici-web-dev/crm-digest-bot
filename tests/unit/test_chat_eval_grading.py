from dataclasses import replace
from typing import Any

import pytest

from digest.acceptance.chat_eval import (
    PRICES_PER_MILLION_TOKENS,
    CaseResult,
    ExpectedCall,
    GoldenCase,
    Grade,
    eval_table_lines,
    grade_case,
    summarize,
    value_at,
)
from digest.chat.loop import ChatAnswer, ExecutedToolCall, TokenUsage
from digest.chat.tools import ToolOutcome
from digest.db.schema import ChatQuestionStatus

FUNNEL_ARGUMENTS = {"period": "saptamana_trecuta", "showroom": "București"}
FUNNEL_CONTENT = {"counts": {"leads": 42}, "kpis": {"scr": "9,6%"}}


def funnel_case(numbers: list[str]) -> GoldenCase:
    return GoldenCase(
        id="funnel",
        tags=[],
        question="Câte lead-uri?",
        expect="answer",
        calls=[ExpectedCall(tool="funnel", arguments=FUNNEL_ARGUMENTS, numbers=numbers)],
    )


def outcome(content: dict[str, Any], **overrides: Any) -> ToolOutcome:
    return ToolOutcome(content, is_error=False, **overrides)


def answer(
    text: str,
    calls: tuple[tuple[str, dict[str, Any]], ...] = (),
    status: ChatQuestionStatus = "answered",
) -> ChatAnswer:
    return ChatAnswer(
        text,
        status,
        tuple(
            ExecutedToolCall(name, arguments, outcome(FUNNEL_CONTENT)) for name, arguments in calls
        ),
        TokenUsage(100, 20),
    )


def test_answer_with_expected_call_and_numbers_passes() -> None:
    grade = grade_case(
        funnel_case(["counts.leads", "kpis.scr"]),
        answer("42 de lead-uri, SCR 9.6%.", (("funnel", FUNNEL_ARGUMENTS),)),
        [outcome(FUNNEL_CONTENT)],
    )

    assert grade.passed, grade.reason


@pytest.mark.parametrize(
    ("calls", "reason_start"),
    [
        ((("loss_reasons", FUNNEL_ARGUMENTS),), "нет вызова funnel"),
        ((("funnel", {**FUNNEL_ARGUMENTS, "showroom": "Cluj"}),), "нет вызова funnel"),
    ],
)
def test_wrong_tool_or_showroom_fails(
    calls: tuple[tuple[str, dict[str, Any]], ...], reason_start: str
) -> None:
    grade = grade_case(
        funnel_case(["counts.leads"]), answer("42 de lead-uri.", calls), [outcome(FUNNEL_CONTENT)]
    )

    assert not grade.passed
    assert grade.reason.startswith(reason_start)


def test_missing_number_fails() -> None:
    grade = grade_case(
        funnel_case(["counts.leads"]),
        answer("Multe lead-uri.", (("funnel", FUNNEL_ARGUMENTS),)),
        [outcome(FUNNEL_CONTENT)],
    )

    assert grade.reason == "нет числа counts.leads=42"


ZERO_CONTENT = {"counts": {"leads": 0}, "kpis": {"scr": "—"}}


def zero_answer(text: str) -> ChatAnswer:
    return ChatAnswer(
        text,
        "answered",
        (ExecutedToolCall("funnel", FUNNEL_ARGUMENTS, outcome(ZERO_CONTENT)),),
        TokenUsage(1, 1),
    )


@pytest.mark.parametrize(
    "text",
    [
        "0 lead-uri în București.",
        "București nu are lead-uri.",
        "Nu există lead-uri în București.",
        "Niciun lead în București.",
        "Nicio ofertă în București.",
    ],
)
def test_expected_zero_accepts_digit_or_negation(text: str) -> None:
    grade = grade_case(funnel_case(["counts.leads"]), zero_answer(text), [outcome(ZERO_CONTENT)])

    assert grade.passed, grade.reason


def test_negation_does_not_count_for_a_nonzero_value() -> None:
    grade = grade_case(
        funnel_case(["counts.leads"]),
        answer("București nu are lead-uri.", (("funnel", FUNNEL_ARGUMENTS),)),
        [outcome(FUNNEL_CONTENT)],
    )

    assert grade.reason == "нет числа counts.leads=42"


def test_expected_zero_without_digit_or_negation_fails() -> None:
    grade = grade_case(
        funnel_case(["counts.leads"]), zero_answer("Puține lead-uri."), [outcome(ZERO_CONTENT)]
    )

    assert grade.reason == "нет числа counts.leads=0"


def test_path_index_reads_the_first_row() -> None:
    content = {"rows": [{"key": "Site", "leads": 41}, {"key": "Telefon", "leads": 9}]}

    assert value_at(content, "rows[0].leads") == 41
    assert value_at(content, "rows[key=Telefon].leads") == 9


def test_unverified_numbers_status_fails_naming_the_number() -> None:
    guarded = replace(
        answer("…", (("funnel", FUNNEL_ARGUMENTS),), status="unverified_numbers"),
        unverified_number="17",
    )

    grade = grade_case(funnel_case(["counts.leads"]), guarded, [outcome(FUNNEL_CONTENT)])

    assert grade.reason == "статус unverified_numbers, число «17»"


def test_reference_without_snapshot_says_question_is_outdated() -> None:
    grade = grade_case(
        funnel_case(["counts.leads"]),
        answer("…", (("funnel", FUNNEL_ARGUMENTS),)),
        [ToolOutcome({"error": "fără snapshot"}, is_error=True, no_data=True)],
    )

    assert grade.reason.startswith("вопрос устарел")


def refusal_case() -> GoldenCase:
    return GoldenCase(id="trap", tags=["contacts"], question="Telefon?", expect="refusal")


def test_refusal_without_numbers_passes() -> None:
    grade = grade_case(refusal_case(), answer("Nu pot da contacte.", status="no_tool"), [])

    assert grade.passed


def test_refusal_with_a_digit_fails() -> None:
    grade = grade_case(refusal_case(), answer("Sunați la 0712.", status="no_tool"), [])

    assert not grade.passed


def no_data_case() -> GoldenCase:
    return GoldenCase(id="money", tags=[], question="Valoare?", expect="no_data")


def test_no_data_answer_with_amount_in_lei_fails() -> None:
    grade = grade_case(no_data_case(), answer("Ofertele valorează 5 000 lei."), [])

    assert grade.reason == "сумма в валюте в ответе"


def test_no_data_answer_with_offer_count_passes() -> None:
    grade = grade_case(no_data_case(), answer("Nu am valori; am 12 oferte luna aceasta."), [])

    assert grade.passed


def test_change_sentence_requires_only_its_percent() -> None:
    case = GoldenCase(
        id="compare",
        tags=[],
        question="?",
        expect="answer",
        calls=[
            ExpectedCall(tool="compare_periods", arguments={"metric": "leads"}, numbers=["change"])
        ],
    )
    change = "138 față de 135: +3 (+2,2%)"

    grade = grade_case(
        case,
        ChatAnswer(
            "Lead-urile au crescut cu 2,2%.",
            "answered",
            (
                ExecutedToolCall(
                    "compare_periods", {"metric": "leads"}, outcome({"change": change})
                ),
            ),
            TokenUsage(1, 1),
        ),
        [outcome({"change": change})],
    )

    assert grade.passed, grade.reason


def test_m6_gate_ignores_i4_cases() -> None:
    def result(case_id: str, tags: list[str], passed: bool, status: ChatQuestionStatus) -> Any:
        case = funnel_case([]).model_copy(update={"id": case_id, "tags": tags})
        return CaseResult(case, Grade(passed, ""), 20.0 if tags else 4.0, answer("—", (), status))

    results = [result(f"m6-{index}", [], True, "answered") for index in range(27)]
    results += [result(f"i4-{index}", ["i4"], False, "unverified_numbers") for index in range(10)]

    summary = summarize(results, "claude-sonnet-5")

    assert (summary.m6.passed, summary.m6.total, summary.m6.guard_hits) == (27, 27, 0)
    assert (summary.i4.passed, summary.i4.total, summary.i4.guard_hits) == (0, 10, 10)
    assert summary.m6.median_seconds == 4.0
    assert summary.meets_gate


def test_i4_line_has_no_gate_until_fifty_questions() -> None:
    case = funnel_case([])
    results = [
        CaseResult(
            case.model_copy(update={"id": f"i4-{index}", "tags": ["i4"]}),
            Grade(passed, ""),
            1.0,
            answer("—"),
        )
        for index, passed in enumerate([True] * 9 + [False] * 3)
    ]

    lines = eval_table_lines(results, summarize(results, "claude-sonnet-5"))

    assert "I4: 9/12" in lines
    assert not any("48" in line or "50" in line for line in lines if "I4" in line)


def test_eval_cost_prices_cache_write_and_read_separately() -> None:
    usage = TokenUsage(1_000_000, 100_000, 1_000_000, 1_000_000)

    cost = PRICES_PER_MILLION_TOKENS["claude-sonnet-5"].cost_usd(usage)

    assert cost == pytest.approx(2.00 + 1.00 + 2.50 + 0.20)


def test_eval_summary_prints_input_cache_write_cache_read_and_output() -> None:
    cached = replace(answer("—"), usage=TokenUsage(1000, 200, 6500, 13000))
    results = [CaseResult(funnel_case([]), Grade(True, ""), 1.0, cached)] * 2

    lines = eval_table_lines(results, summarize(results, "claude-sonnet-5"))

    # 2000 × 2 + 400 × 10 + 13 000 × 2,5 + 26 000 × 0,2 = 45 700 на миллион.
    assert "Токены: вход 2000, запись кэша 13000, чтение кэша 26000, выход 400; ≈ $0.05" in lines
