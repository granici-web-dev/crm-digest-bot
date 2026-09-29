from collections import Counter
from datetime import date
from pathlib import Path

from digest.acceptance.chat_eval import I4_TAG, load_golden_cases
from digest.chat.tools import ARGUMENT_MODELS
from digest.config import AppConfig

GOLDEN_FILE = Path(__file__).resolve().parents[1] / "eval" / "chat-golden.yaml"
TODAY = date(2026, 9, 29)


# Шесть инструментов M6 (docs/success-criteria.md, «1. MVP»); новые инструменты проверяет набор I4.
M6_TOOLS = frozenset(
    {
        "funnel",
        "manager_kpi",
        "compare_periods",
        "loss_reasons",
        "overdue_followups",
        "untouched_leads",
    }
)


def test_golden_file_has_thirty_cases_with_required_coverage() -> None:
    cases = [case for case in load_golden_cases(GOLDEN_FILE) if I4_TAG not in case.tags]
    tags = Counter(tag for case in cases for tag in case.tags)
    tools = Counter(call.tool for case in cases for call in case.calls)

    assert len(cases) == 30
    assert set(tools) == M6_TOOLS
    assert min(tools.values()) >= 2
    assert tools["compare_periods"] >= 2
    assert tags["contacts"] == 3
    assert tags["offtopic"] == 3
    assert sum(case.expect == "no_data" for case in cases) == 2
    assert tags["followup"] >= 3
    assert tags["specific_day"] >= 1
    assert tags["specific_month"] >= 1


def test_i4_cases_cover_every_new_tool() -> None:
    cases = [case for case in load_golden_cases(GOLDEN_FILE) if I4_TAG in case.tags]
    tools = Counter(call.tool for case in cases for call in case.calls)

    assert len(cases) == 12
    assert set(tools) == set(ARGUMENT_MODELS) - M6_TOOLS
    assert min(tools.values()) >= 3
    # Отказ по неподходящему периоду у каждого инструмента со своим набором периодов.
    refusals = [case for case in cases if "refusal_period" in case.tags]
    assert [case.expect for case in refusals] == ["refusal", "refusal"]
    assert {tag for case in refusals for tag in case.tags} >= {"touches", "repeat"}


def test_golden_file_covers_every_showroom_and_three_consultants(app_config: AppConfig) -> None:
    arguments = [
        options
        for case in load_golden_cases(GOLDEN_FILE)
        for call in case.calls
        for options in call.argument_options
    ]

    assert {argument.get("showroom") for argument in arguments} >= set(
        app_config.status_mapping.showrooms
    )
    assert (
        len({argument["manager"] for argument in arguments if "manager" in argument} - {"toti"})
        >= 3
    )


def test_every_expected_argument_set_is_valid_for_its_tool(app_config: AppConfig) -> None:
    for case in load_golden_cases(GOLDEN_FILE):
        for call in case.calls:
            for arguments in call.argument_options:
                ARGUMENT_MODELS[call.tool].model_validate(
                    arguments,
                    context={"config": app_config, "today": TODAY, "earliest_day": None},
                )
