import json
from dataclasses import replace
from datetime import date, timedelta
from itertools import product
from typing import Any

import pandas as pd
import pytest

from digest.chat.tools import (
    ALL_MANAGERS,
    ALL_SHOWROOMS,
    TOOL_FUNCTIONS,
    ToolData,
    run_tool,
    tool_definitions,
)
from digest.config import CHAT_TOOL_NAMES, KPI_NAMES, AppConfig, Manager, ManagerRoster
from digest.db.lead_frame import SnapshotMissingError
from digest.metrics.chat_periods import CHAT_PERIODS, named_period_window
from digest.metrics.cockpit import manager_cockpit_table
from digest.metrics.daily_checks import overdue_revenire_by_manager, untouched_leads
from digest.metrics.frame import prepare_lead_frame
from digest.metrics.kpi import COUNT_NAMES, kpis_from, lead_counts, lead_counts_by_showroom
from digest.metrics.monthly import monthly_funnel
from digest.metrics.weekly import (
    converted_count,
    converted_count_by_showroom,
    loss_reasons_in_window,
    weekly_funnel,
)
from digest.reports.render import percent_one_decimal
from factories import etalon_lead_rows, load_etalon

TODAY = date(2026, 6, 3)
END_OF_MAY = date(2026, 5, 31)
LEAD_ID_OFFSET = 7_000_000


@pytest.fixture(scope="module")
def etalon_config(app_config: AppConfig) -> AppConfig:
    etalon = load_etalon()
    roster = ManagerRoster(
        managers=[
            Manager(id=manager_id, name=name, showroom=None, active=True)
            for name, manager_id in etalon["managers"].items()
        ]
    )
    return app_config.model_copy(update={"managers": roster})


@pytest.fixture(scope="module")
def lead_frame(etalon_config: AppConfig) -> pd.DataFrame:
    rows = etalon_lead_rows(load_etalon(), etalon_config.status_mapping)
    for row in rows:
        # id заметно отличаются от любых счётчиков: утечку id видно в тексте результата.
        row["lead_id"] += LEAD_ID_OFFSET
        if row["category"] == "WON":
            row["converted_at"] = row["created_at"] + timedelta(days=5)
    return prepare_lead_frame(rows, etalon_config)


def tool_data(
    config: AppConfig, lead_frame: pd.DataFrame, missing: frozenset[date] = frozenset()
) -> ToolData:
    async def load_frame(snapshot_date: date) -> pd.DataFrame:
        if snapshot_date in missing:
            raise SnapshotMissingError(str(snapshot_date))
        return lead_frame

    return ToolData(TODAY, TODAY, load_frame, config)


async def run(
    name: str, arguments: dict[str, Any], config: AppConfig, lead_frame: pd.DataFrame
) -> dict[str, Any]:
    outcome = await run_tool(name, arguments, tool_data(config, lead_frame))
    assert not outcome.is_error, outcome.content
    return outcome.content


def may(config: AppConfig) -> Any:
    return named_period_window("luna_trecuta", TODAY, config.status_mapping.time)


async def test_funnel_matches_metrics_and_monthly_report(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run(
        "funnel", {"period": "luna_trecuta", "showroom": ALL_SHOWROOMS}, etalon_config, lead_frame
    )

    counts = lead_counts(lead_frame, may(etalon_config), END_OF_MAY, etalon_config)
    assert counts == monthly_funnel(lead_frame, END_OF_MAY, etalon_config).company
    assert content["counts"] == {name: getattr(counts, name) for name in COUNT_NAMES}
    assert content["contracts"] == converted_count(lead_frame, may(etalon_config)) > 0
    kpis = kpis_from(counts)
    assert content["kpis"] == {name: percent_one_decimal(getattr(kpis, name)) for name in KPI_NAMES}
    assert content["snapshot_date"] == "31.05.2026"
    assert content["data_as_of"] is None


async def test_funnel_for_showroom_matches_metrics(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run(
        "funnel", {"period": "luna_trecuta", "showroom": "București"}, etalon_config, lead_frame
    )

    window = may(etalon_config)
    by_showroom = lead_counts_by_showroom(lead_frame, window, END_OF_MAY, etalon_config)
    assert content["counts"]["leads"] == by_showroom["București"].leads > 0
    contracts = converted_count_by_showroom(lead_frame, window, etalon_config)["București"]
    assert content["contracts"] == contracts


async def test_last_week_funnel_matches_weekly_report(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run(
        "funnel",
        {"period": "saptamana_trecuta", "showroom": ALL_SHOWROOMS},
        etalon_config,
        lead_frame,
    )

    weekly = weekly_funnel(lead_frame, END_OF_MAY, etalon_config).counts
    assert content["counts"]["leads"] == weekly.leads > 0
    assert content["counts"]["offers"] == weekly.offers


async def test_manager_kpi_matches_cockpit_row(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run(
        "manager_kpi",
        {"manager": "Dragoi Mihaela", "period": "luna_trecuta"},
        etalon_config,
        lead_frame,
    )

    table = manager_cockpit_table(lead_frame, may(etalon_config), END_OF_MAY, etalon_config)
    row = table[table["name"].eq("Dragoi Mihaela")].iloc[0]
    assert content["counts"] == {name: int(row[name]) for name in COUNT_NAMES}
    assert content["kpis"] == {name: percent_one_decimal(row[name]) for name in KPI_NAMES}
    assert content["meets_target"] == {
        name: row[f"{name}_meets_target"] for name in etalon_config.kpi.targets
    }


async def test_compare_periods_matches_lead_counts_of_both_windows(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run(
        "compare_periods",
        {
            "metric": "leads",
            "period_a": "saptamana_trecuta",
            "period_b": "luna_trecuta",
            "showroom": ALL_SHOWROOMS,
        },
        etalon_config,
        lead_frame,
    )

    time_settings = etalon_config.status_mapping.time
    last_week = named_period_window("saptamana_trecuta", TODAY, time_settings)
    week_leads = lead_counts(lead_frame, last_week, END_OF_MAY, etalon_config).leads
    month_leads = lead_counts(lead_frame, may(etalon_config), END_OF_MAY, etalon_config).leads
    assert (content["period_a"]["value"], content["period_b"]["value"]) == (
        week_leads,
        month_leads,
    )
    assert content["change_a_vs_b"].startswith("(−")


async def test_compare_periods_of_kpi_gives_no_relative_change(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run(
        "compare_periods",
        {
            "metric": "scr",
            "period_a": "saptamana_trecuta",
            "period_b": "luna_trecuta",
            "showroom": ALL_SHOWROOMS,
        },
        etalon_config,
        lead_frame,
    )

    month_counts = lead_counts(lead_frame, may(etalon_config), END_OF_MAY, etalon_config)
    assert content["period_b"]["value"] == percent_one_decimal(kpis_from(month_counts).scr)
    assert content["change_a_vs_b"] is None


async def test_loss_reasons_match_metrics(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run(
        "loss_reasons",
        {"period": "luna_trecuta", "showroom": ALL_SHOWROOMS},
        etalon_config,
        lead_frame,
    )

    losses = loss_reasons_in_window(lead_frame, may(etalon_config), etalon_config)
    reasons = etalon_config.status_mapping.categories.LOST.reasons
    assert content["total"] == losses.total > 0
    assert content["by_reason"] == {
        reasons[key].label: losses.reason_total(key) for key in losses.reasons_by_count
    }
    assert sum(showroom["total"] for showroom in content["by_showroom"].values()) == losses.total


async def test_loss_reasons_for_showroom_match_metrics(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run(
        "loss_reasons", {"period": "luna_trecuta", "showroom": "Cluj"}, etalon_config, lead_frame
    )

    losses = loss_reasons_in_window(lead_frame, may(etalon_config), etalon_config)
    assert content["total"] == losses.showroom_total("Cluj")


async def test_overdue_followups_match_metrics(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run("overdue_followups", {"manager": ALL_MANAGERS}, etalon_config, lead_frame)

    overdue = overdue_revenire_by_manager(lead_frame, TODAY, etalon_config)
    assert content["lead_count"] == overdue.lead_count > 0
    assert [group["lead_count"] for group in content["by_manager"]] == [
        group.lead_count for group in overdue.groups
    ]


async def test_overdue_followups_for_one_manager(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run(
        "overdue_followups", {"manager": "Moaca Andreea"}, etalon_config, lead_frame
    )

    overdue = overdue_revenire_by_manager(lead_frame, TODAY, etalon_config)
    expected = [group for group in overdue.groups if group.manager_name == "Moaca Andreea"]
    assert content["lead_count"] == (expected[0].lead_count if expected else 0)


async def test_untouched_leads_match_metrics(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run("untouched_leads", {}, etalon_config, lead_frame)

    untouched = untouched_leads(lead_frame, TODAY, etalon_config)
    assert content["lead_count"] == untouched.lead_count
    assert [group["lead_count"] for group in content["by_manager"]] == [
        group.lead_count for group in untouched.groups
    ]


def all_tool_calls(config: AppConfig) -> list[tuple[str, dict[str, Any]]]:
    showrooms = (ALL_SHOWROOMS, *config.status_mapping.showrooms)
    managers = [manager.name for manager in config.managers.managers]
    calls: list[tuple[str, dict[str, Any]]] = [("untouched_leads", {})]
    calls += [("overdue_followups", {"manager": name}) for name in (ALL_MANAGERS, *managers)]
    for period, showroom in product(CHAT_PERIODS, showrooms):
        calls += [
            ("funnel", {"period": period, "showroom": showroom}),
            ("loss_reasons", {"period": period, "showroom": showroom}),
            (
                "compare_periods",
                {
                    "metric": "offers",
                    "period_a": period,
                    "period_b": "luna_trecuta",
                    "showroom": showroom,
                },
            ),
        ]
    calls += [
        ("manager_kpi", {"manager": name, "period": period})
        for name, period in product(managers, CHAT_PERIODS)
    ]
    return calls


async def test_tool_results_contain_no_lead_ids_or_client_data(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    lead_ids = {str(lead_id) for lead_id in lead_frame["lead_id"]}
    for name, arguments in all_tool_calls(etalon_config):
        outcome = await run_tool(name, arguments, tool_data(etalon_config, lead_frame))
        serialized = json.dumps(outcome.content, ensure_ascii=False)

        assert not outcome.is_error, (name, arguments, outcome.content)
        assert "lead_id" not in serialized
        assert not any(lead_id in serialized for lead_id in lead_ids), (name, arguments)
        assert "@" not in serialized
        assert "+40" not in serialized


async def test_current_period_before_first_snapshot_of_it_has_no_data(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    data = replace(tool_data(etalon_config, lead_frame), latest_snapshot_date=TODAY - timedelta(1))

    outcome = await run_tool("funnel", {"period": "azi", "showroom": ALL_SHOWROOMS}, data)

    assert outcome.is_error
    assert outcome.content == {
        "error": "Nu există încă date pentru 03.06.2026: ultimul snapshot este din 02.06.2026."
    }


async def test_partial_period_states_snapshot_date(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    data = replace(tool_data(etalon_config, lead_frame), latest_snapshot_date=TODAY - timedelta(1))

    outcome = await run_tool(
        "funnel", {"period": "saptamana_curenta", "showroom": ALL_SHOWROOMS}, data
    )

    assert outcome.data_as_of == date(2026, 6, 2)
    assert outcome.content["data_as_of"] == "02.06.2026"


async def test_closed_period_without_its_snapshot_has_no_data_and_no_substitute(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    data = tool_data(etalon_config, lead_frame, missing=frozenset({END_OF_MAY}))

    outcome = await run_tool(
        "loss_reasons", {"period": "luna_trecuta", "showroom": ALL_SHOWROOMS}, data
    )

    assert outcome.is_error
    assert outcome.content == {"error": "Nu există snapshot pentru 31.05.2026."}


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("manager_kpi", {"manager": "Palega Andrei", "period": "azi"}),
        ("manager_kpi", {"manager": ALL_MANAGERS, "period": "azi"}),
        ("funnel", {"period": "azi", "showroom": "Iași"}),
        ("funnel", {"period": "ieri"}),
        ("funnel", {"period": "maine", "showroom": ALL_SHOWROOMS}),
        ("funnel", {"period": "azi", "showroom": ALL_SHOWROOMS, "sql": "select 1"}),
        (
            "compare_periods",
            {"metric": "revenue", "period_a": "azi", "period_b": "ieri", "showroom": "toate"},
        ),
        ("run_sql", {}),
    ],
)
async def test_invalid_arguments_are_tool_errors(
    app_config: AppConfig, lead_frame: pd.DataFrame, name: str, arguments: dict[str, Any]
) -> None:
    outcome = await run_tool(name, arguments, tool_data(app_config, lead_frame))

    assert outcome.is_error


def test_every_tool_name_has_a_function() -> None:
    assert set(TOOL_FUNCTIONS) == set(CHAT_TOOL_NAMES)


def test_tool_definitions_are_strict_with_config_enums(app_config: AppConfig) -> None:
    definitions = {definition["name"]: definition for definition in tool_definitions(app_config)}

    assert set(definitions) == set(app_config.modules.chat.tools)
    for definition in definitions.values():
        schema: Any = definition["input_schema"]
        assert definition.get("strict") is True
        assert schema["required"] == list(schema["properties"])
        assert schema["additionalProperties"] is False
    manager_enum = definitions["manager_kpi"]["input_schema"]["properties"]["manager"]["enum"]  # type: ignore[index]
    assert "Dragoi Mihaela" in manager_enum
    assert "Palega Andrei" not in manager_enum
    assert "Marketing Sofa" not in manager_enum
    assert "Potinga Dima" not in manager_enum
    showroom_enum = definitions["funnel"]["input_schema"]["properties"]["showroom"]["enum"]  # type: ignore[index]
    assert showroom_enum == [ALL_SHOWROOMS, "Brașov", "București", "Cluj"]
