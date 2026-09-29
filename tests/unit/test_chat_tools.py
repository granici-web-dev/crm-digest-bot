import json
from datetime import date, datetime, timedelta
from itertools import product
from typing import Any, cast, get_args

import pandas as pd
import pytest

from digest.chat.tools import (
    ALL_MANAGERS,
    ALL_SHOWROOMS,
    TOOL_FUNCTIONS,
    ToolData,
    count_comparison,
    kpi_comparison,
    run_tool,
    tool_definitions,
)
from digest.config import KPI_NAMES, AppConfig, ChatToolName, Manager, ManagerRoster
from digest.db.lead_frame import SnapshotMissingError
from digest.metrics.chat_periods import CHAT_PERIODS, chat_period_window
from digest.metrics.daily import PreviousSnapshot, daily_window
from digest.metrics.daily_checks import overdue_revenire_by_manager, untouched_leads
from digest.metrics.frame import prepare_lead_frame
from digest.metrics.kpi import COUNT_NAMES, kpis_from, lead_counts, lead_counts_by_showroom
from digest.metrics.monthly import (
    monthly_funnel,
    monthly_repeat_clients,
    monthly_source_conversion,
)
from digest.metrics.touches import manager_touches, touch_snapshot_dates
from digest.metrics.weekly import (
    converted_count,
    converted_count_by_showroom,
    loss_reasons_in_window,
    week_days,
    weekly_funnel,
)
from digest.reports.render import percent_one_decimal
from factories import (
    BUCHAREST,
    etalon_lead_rows,
    load_etalon,
    make_lead_links,
    make_snapshot_row,
    raw_repository_config,
)

TODAY = date(2026, 6, 3)
END_OF_MAY = date(2026, 5, 31)
LEAD_ID_OFFSET = 7_000_000
KPI_NAMES_UPPER = tuple(name.upper() for name in KPI_NAMES)


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


SNAPSHOT_DATES = (END_OF_MAY, date(2026, 6, 2), TODAY)


def tool_data(
    config: AppConfig,
    lead_frame: pd.DataFrame,
    snapshot_dates: tuple[date, ...] = SNAPSHOT_DATES,
) -> ToolData:
    async def load_frame(snapshot_date: date) -> pd.DataFrame:
        if snapshot_date not in snapshot_dates:
            raise SnapshotMissingError(str(snapshot_date))
        return lead_frame

    return ToolData(
        TODAY, snapshot_dates, load_frame, config, make_lead_links(config.status_mapping)
    )


async def run(
    name: str, arguments: dict[str, Any], config: AppConfig, lead_frame: pd.DataFrame
) -> dict[str, Any]:
    outcome = await run_tool(name, arguments, tool_data(config, lead_frame))
    assert not outcome.is_error, outcome.content
    return outcome.content


def may(config: AppConfig) -> Any:
    return chat_period_window("luna_trecuta", TODAY, config.status_mapping.time)


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


async def test_manager_kpi_matches_etalon_consultant(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run(
        "manager_kpi",
        {"manager": "Dragoi Mihaela", "period": "luna_trecuta"},
        etalon_config,
        lead_frame,
    )

    # Эталон мая, expected_by_agent["Dragoi Mihaela"], сверен вручную с mefi.
    assert content["counts"] == {
        "leads": 76,
        "irr_leads": 13,
        "useful": 63,
        "clienti": 2,
        "offers": 23,
        "nar": 13,
        "buget": 25,
        "pnp": 7,
        "showroom_visits": 36,
        "clienti_from_showroom": 2,
        "active_offers_14": 0,
        "unmapped": 0,
    }
    assert content["kpis"] == {
        "scr": "3,2%",
        "l2o": "36,5%",
        "o2c": "8,7%",
        "cdr": "82,9%",
        "plr": "50,0%",
        "sc": "5,6%",
        "pfr": "11,1%",
        "acr": "0,0%",
        "irr": "17,1%",
    }
    assert set(content["meets_target"]) == set(etalon_config.kpi.targets)
    # Цели config/kpi.yaml: SCR 2/63 = 3,17% против 10% это −6,8 pp; CDR (76 − 13)/76 = 82,89%
    # против 90% это −7,1 pp; ACR и IRR ниже потолка.
    assert content["vs_target"] == {
        "scr": "−6,8 pp",
        "l2o": "−13,5 pp",
        "o2c": "−11,3 pp",
        "cdr": "−7,1 pp",
        "plr": "+25,0 pp",
        "sc": "−14,4 pp",
        "pfr": "+1,1 pp",
        "acr": "−20,0 pp",
        "irr": "−2,9 pp",
    }
    assert (content["targets_met"], content["targets_total"]) == (2, 9)
    assert [name for name, met in content["meets_target"].items() if met] == ["acr", "irr"]


async def test_manager_without_leads_has_no_gap_to_target(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    idle = Manager(id=990_001, name="Consultant Fără Lead-uri", showroom=None, active=True)
    roster = ManagerRoster(managers=[*etalon_config.managers.managers, idle])
    config = etalon_config.model_copy(update={"managers": roster})

    content = await run(
        "manager_kpi", {"manager": idle.name, "period": "luna_trecuta"}, config, lead_frame
    )

    assert content["counts"]["leads"] == 0
    assert content["vs_target"] == dict.fromkeys(config.kpi.targets)
    assert content["targets_met"] == 0


def test_manager_kpi_description_names_every_ceiling_kpi(etalon_config: AppConfig) -> None:
    # «Минус значит в пределах» верно только для KPI с потолком; список в описании сверяется с
    # направлением целей kpi.yaml.
    ceilings = {
        name.upper()
        for name, target in etalon_config.kpi.targets.items()
        if target.direction == "lower"
    }
    description = etalon_config.modules.chat.tools["manager_kpi"]

    assert f"({', '.join(sorted(ceilings, key=list(KPI_NAMES_UPPER).index))})" in description


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
    last_week = chat_period_window("saptamana_trecuta", TODAY, time_settings)
    week_leads = lead_counts(lead_frame, last_week, END_OF_MAY, etalon_config).leads
    month_leads = lead_counts(lead_frame, may(etalon_config), END_OF_MAY, etalon_config).leads
    assert (content["period_a"]["value"], content["period_b"]["value"]) == (
        week_leads,
        month_leads,
    )
    # 93 lead-uri în ultima săptămână din mai față de 300 în mai: 93 / 300 − 1 = −69,0%.
    assert (week_leads, month_leads) == (93, 300)
    assert content["change"] == "93 față de 300: −207 (−69,0%)"
    assert (content["difference"], content["direction"]) == (207, "scădere")
    assert (content["period_a"]["day_count"], content["period_a"]["days_with_data"]) == (7, 7)
    assert (content["period_b"]["day_count"], content["period_b"]["days_with_data"]) == (31, 31)


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
    week = chat_period_window("saptamana_trecuta", TODAY, etalon_config.status_mapping.time)
    week_scr = kpis_from(lead_counts(lead_frame, week, END_OF_MAY, etalon_config)).scr
    month_scr = kpis_from(month_counts).scr
    assert week_scr is not None
    assert month_scr is not None
    assert round((week_scr - month_scr) * 100, 1) == -1.8
    assert content["change"] == (
        f"{content['period_a']['value']} față de {content['period_b']['value']}: −1,8 pp"
    )
    assert (content["difference"], content["direction"]) == ("1,8 pp", "scădere")


def test_change_from_zero_base_names_both_values_without_percent() -> None:
    comparison = count_comparison(5, 0)

    assert comparison["change"] == "5 față de 0: +5; procentul nu se calculează, baza este 0"
    assert (comparison["difference"], comparison["direction"]) == (5, "creștere")


def test_kpi_change_is_absent_when_one_period_has_no_kpi() -> None:
    assert kpi_comparison(None, 0.25) == {"difference": None, "direction": None, "change": None}


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
        reasons[key].label: {
            "lead_count": losses.reason_total(key),
            "share": percent_one_decimal(losses.reason_total(key) / losses.total),
        }
        for key in losses.reasons_by_count
    }
    # 65 irelevante из 270 потерь мая = 24,1%.
    assert content["total"] == 270
    assert content["by_reason"]["Irelevant"] == {"lead_count": 65, "share": "24,1%"}
    assert sum(showroom["total"] for showroom in content["by_showroom"].values()) == losses.total
    assert content["showroom_count"] == sum(
        1 for key in losses.by_showroom if key is not None and losses.showroom_total(key)
    )


async def test_loss_reasons_for_showroom_match_metrics(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run(
        "loss_reasons", {"period": "luna_trecuta", "showroom": "Cluj"}, etalon_config, lead_frame
    )

    losses = loss_reasons_in_window(lead_frame, may(etalon_config), etalon_config)
    assert content["total"] == losses.showroom_total("Cluj")
    assert {reason["lead_count"] for reason in content["by_reason"].values()} <= set(
        losses.by_showroom["Cluj"].values()
    )
    for reason in content["by_reason"].values():
        assert reason["share"] == percent_one_decimal(reason["lead_count"] / content["total"])


async def test_overdue_followups_match_metrics(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run("overdue_followups", {"manager": ALL_MANAGERS}, etalon_config, lead_frame)

    overdue = overdue_revenire_by_manager(lead_frame, TODAY, etalon_config)
    assert content["lead_count"] == overdue.lead_count > 0
    assert [group["lead_count"] for group in content["by_manager"]] == [
        group.lead_count for group in overdue.groups
    ]
    assert content["manager_count"] == sum(
        1 for group in overdue.groups if group.manager_name is not None
    )


async def test_overdue_followups_for_one_manager(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run(
        "overdue_followups", {"manager": "Dragoi Mihaela"}, etalon_config, lead_frame
    )

    assert (content["lead_count"], content["max_days_overdue"]) == (5, 22)


async def test_overdue_followups_for_manager_without_overdue_is_zero(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run(
        "overdue_followups", {"manager": "Moaca Andreea"}, etalon_config, lead_frame
    )

    assert (content["lead_count"], content["max_days_overdue"]) == (0, None)


SEPTEMBER_REPORT_DATE = date(2026, 9, 25)


@pytest.mark.parametrize("by", ["source_name", "utm_campanie"])
async def test_source_breakdown_total_equals_funnel_leads(
    etalon_config: AppConfig, lead_frame: pd.DataFrame, by: str
) -> None:
    for showroom in (ALL_SHOWROOMS, *etalon_config.status_mapping.showrooms):
        arguments = {"period": "luna_trecuta", "showroom": showroom}
        breakdown = await run(
            "source_breakdown", {**arguments, "by": by}, etalon_config, lead_frame
        )
        funnel = await run("funnel", arguments, etalon_config, lead_frame)

        assert breakdown["total"]["leads"] == funnel["counts"]["leads"] > 0, showroom
        assert breakdown["total"]["scr"] == funnel["kpis"]["scr"], showroom
        row_leads = sum(row["leads"] for row in breakdown["rows"])
        without_key = breakdown["without_key"]
        assert (
            row_leads + (0 if without_key is None else without_key["leads"])
            == (breakdown["total"]["leads"])
        )
        assert breakdown["row_count"] == len(breakdown["rows"])


async def test_source_breakdown_rows_match_m7_for_last_month(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    conversion = monthly_source_conversion(lead_frame, END_OF_MAY, etalon_config)
    by_source = await run(
        "source_breakdown",
        {"period": "luna_trecuta", "by": "source_name", "showroom": ALL_SHOWROOMS},
        etalon_config,
        lead_frame,
    )
    by_campaign = await run(
        "source_breakdown",
        {"period": "luna_trecuta", "by": "utm_campanie", "showroom": ALL_SHOWROOMS},
        etalon_config,
        lead_frame,
    )

    rows = {row["key"]: row for row in by_source["rows"]}
    assert conversion.by_source.rows
    for m7_row in conversion.by_source.rows:
        assert rows[m7_row.key]["leads"] == m7_row.counts.leads
        assert rows[m7_row.key]["scr"] == percent_one_decimal(m7_row.kpis.scr)
    assert by_campaign["leads_with_key"] == conversion.leads_with_campaign > 0
    assert by_campaign["key_share"] == percent_one_decimal(conversion.campaign_share)
    assert "leads_with_key" not in by_source


async def test_source_breakdown_campaign_label_hides_phone(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    campaigns = lead_frame.copy()
    campaigns["utm_campanie"] = None
    campaigns.loc[campaigns.index[:3], "utm_campanie"] = "Promo +40700000001"
    campaigns.loc[campaigns.index[3:5], "utm_campanie"] = "Promo 30"
    campaigns.loc[campaigns.index[5:6], "utm_campanie"] = "Toamna"

    outcome = await run_tool(
        "source_breakdown",
        {"period": {"year": 2026, "month": 5}, "by": "utm_campanie", "showroom": ALL_SHOWROOMS},
        tool_data(etalon_config, campaigns),
    )

    keys = [row["key"] for row in outcome.content["rows"]]
    hidden = etalon_config.status_mapping.hidden_campaign_label
    assert sorted(keys) == sorted([hidden, "Promo 30", "Toamna"])
    assert "+40" not in json.dumps(outcome.content, ensure_ascii=False)
    assert outcome.masked_names == ("Promo 30",)


async def test_repeat_clients_matches_m11_for_closed_month(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run("repeat_clients", {"period": "luna_trecuta"}, etalon_config, lead_frame)

    m11 = monthly_repeat_clients(lead_frame, END_OF_MAY, etalon_config)
    assert content["clients"] == m11.company.clients > 0
    assert content["repeat"] == m11.company.repeat
    assert content["share"] == percent_one_decimal(m11.company.share)
    assert [reason["client_count"] for reason in content["by_reason"]] == [
        m11.company.by_contact,
        m11.company.by_source,
    ]
    by_showroom = {row["showroom"]: row for row in content["by_showroom"]}
    for showroom, counts in m11.by_showroom.items():
        if showroom is not None and counts.clients:
            assert by_showroom[showroom]["clients"] == counts.clients
    assert content["showroom_count"] == sum(
        1 for showroom, counts in m11.by_showroom.items() if showroom and counts.clients
    )
    assert content["won_without_converted_at"] == m11.won_without_converted_at


async def test_repeat_clients_substituted_snapshot_keeps_period_month(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    outcome = await run_tool(
        "repeat_clients",
        {"period": "luna_trecuta"},
        tool_data(etalon_config, lead_frame, SNAPSHOT_DATES[1:]),
    )

    may_clients = monthly_repeat_clients(lead_frame, END_OF_MAY, etalon_config).company.clients
    june_clients = monthly_repeat_clients(lead_frame, TODAY, etalon_config).company.clients
    assert may_clients != june_clients
    assert outcome.content["clients"] == may_clients
    assert outcome.content["missing_snapshot_for"] == "31.05.2026"


@pytest.mark.parametrize("period", ["saptamana_trecuta", "ultimele_30_zile", {"day": "2026-05-15"}])
async def test_repeat_clients_rejects_week_period(
    etalon_config: AppConfig, lead_frame: pd.DataFrame, period: Any
) -> None:
    outcome = await run_tool(
        "repeat_clients", {"period": period}, tool_data(etalon_config, lead_frame)
    )

    assert outcome.is_error
    assert "se numără pe lună" in outcome.content["error"]


async def test_repeat_clients_result_has_no_contact_keys(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    clients = lead_frame.copy()
    won = clients.index[clients["is_clienti"]][:2]
    clients.loc[won, "contact_phone_key"] = "+40700000001"
    clients.loc[won, "contact_email_key"] = "client1@example.com"
    clients.loc[won[0], "converted_at"] = clients.loc[won[0], "converted_at"] - timedelta(days=1)

    outcome = await run_tool(
        "repeat_clients", {"period": "luna_trecuta"}, tool_data(etalon_config, clients)
    )

    serialized = json.dumps(outcome.content, ensure_ascii=False)
    assert outcome.content["by_reason"][0]["client_count"] >= 1
    assert "+40" not in serialized
    assert "@" not in serialized
    assert "_key" not in serialized


TOUCH_TODAY = date(2026, 9, 29)
LAST_SUNDAY = date(2026, 9, 27)


def touch_lead(lead_id: int, status_name: str, changed_on: date | None = None) -> dict[str, Any]:
    created_at = datetime(2026, 9, 1, 11, 0, tzinfo=BUCHAREST)
    return make_snapshot_row(
        lead_id=lead_id,
        category="ACTIVE_FOLLOWUP" if status_name.startswith("Revenire") else "ACTIVE",
        status_name=status_name,
        created_at=created_at,
        last_contact_at=created_at,
        status_changed_at=(
            None
            if changed_on is None
            else datetime(changed_on.year, changed_on.month, changed_on.day, 12, tzinfo=BUCHAREST)
        ),
    )


def touch_snapshots(config: AppConfig) -> dict[date, pd.DataFrame]:
    rows = {
        date(2026, 9, 20): [touch_lead(1, "IN PROCES"), touch_lead(2, "IN PROCES")],
        date(2026, 9, 24): [
            touch_lead(1, "Revenire 1", date(2026, 9, 23)),
            touch_lead(2, "IN PROCES"),
        ],
        LAST_SUNDAY: [
            touch_lead(1, "Revenire 2", date(2026, 9, 26)),
            touch_lead(2, "Revenire 1", LAST_SUNDAY),
        ],
        date(2026, 9, 28): [
            touch_lead(1, "Revenire 2", date(2026, 9, 26)),
            touch_lead(2, "Revenire 2", date(2026, 9, 28)),
        ],
    }
    return {day: prepare_lead_frame(day_rows, config) for day, day_rows in rows.items()}


def touch_data(config: AppConfig, frames: dict[date, pd.DataFrame]) -> ToolData:
    async def load_frame(snapshot_date: date) -> pd.DataFrame:
        return frames[snapshot_date]

    return ToolData(
        TOUCH_TODAY,
        tuple(sorted(frames)),
        load_frame,
        config,
        make_lead_links(config.status_mapping),
    )


async def test_manager_touches_last_week_matches_w14(app_config: AppConfig) -> None:
    frames = touch_snapshots(app_config)
    outcome = await run_tool(
        "manager_touches",
        {"period": "saptamana_trecuta", "manager": ALL_MANAGERS},
        touch_data(app_config, frames),
    )

    # Как touch_snapshot_chain воскресного прогона w14 за 27.09.
    chain = touch_snapshot_dates(tuple(sorted(frames)), week_days(LAST_SUNDAY)[0], LAST_SUNDAY)
    w14 = manager_touches(
        tuple(PreviousSnapshot(day, frames[day]) for day in chain),
        week_days(LAST_SUNDAY),
        app_config,
    )
    content = outcome.content
    assert content["touch_count"] == w14.touch_count == 3
    assert [level["touch_count"] for level in content["by_level"]] == list(w14.by_level)
    assert [level["status"] for level in content["by_level"]] == [
        "Revenire 1",
        "Revenire 2",
        "Revenire 3",
    ]
    assert {row["manager"]: row["touch_count"] for row in content["by_manager"]} == {
        group.manager_name or "Nepreluate": group.touch_count for group in w14.groups
    }
    assert content["manager_count"] == 1
    assert content["managers_without_touches"] == sum(
        1 for group in w14.groups if group.manager_name and not group.touch_count
    )
    assert (content["days"], content["day_count"]) == ("21.09–27.09.2026", 7)
    assert outcome.snapshot_dates == chain


async def test_manager_touches_single_consultant_uses_of_manager(app_config: AppConfig) -> None:
    data = touch_data(app_config, touch_snapshots(app_config))

    touched = await run_tool(
        "manager_touches", {"period": "saptamana_trecuta", "manager": "Dragoi Mihaela"}, data
    )
    idle = await run_tool(
        "manager_touches", {"period": "saptamana_trecuta", "manager": "Moaca Andreea"}, data
    )

    assert touched.content["manager"] == "Dragoi Mihaela"
    assert touched.content["touch_count"] == 3
    assert "by_manager" not in touched.content
    assert idle.content["touch_count"] == 0
    assert [level["touch_count"] for level in idle.content["by_level"]] == [0, 0, 0]


@pytest.mark.parametrize("period", ["luna_curenta", "ultimele_30_zile", {"year": 2026, "month": 9}])
async def test_manager_touches_rejects_month_period(app_config: AppConfig, period: Any) -> None:
    outcome = await run_tool(
        "manager_touches",
        {"period": period, "manager": ALL_MANAGERS},
        touch_data(app_config, touch_snapshots(app_config)),
    )

    assert outcome.is_error
    assert not outcome.no_data
    assert "zile și săptămâni" in outcome.content["error"]


async def test_manager_touches_without_snapshot_pair_is_no_data(app_config: AppConfig) -> None:
    frames = touch_snapshots(app_config)
    outcome = await run_tool(
        "manager_touches",
        {"period": "ieri", "manager": ALL_MANAGERS},
        touch_data(app_config, {date(2026, 9, 28): frames[date(2026, 9, 28)]}),
    )

    assert outcome.is_error
    assert outcome.no_data
    assert "două snapshoturi CRM pentru 28.09.2026" in outcome.content["error"]


async def test_manager_touches_reports_days_without_snapshot(app_config: AppConfig) -> None:
    frames = touch_snapshots(app_config)
    del frames[date(2026, 9, 20)]

    outcome = await run_tool(
        "manager_touches",
        {"period": "saptamana_trecuta", "manager": ALL_MANAGERS},
        touch_data(app_config, frames),
    )

    content = outcome.content
    assert (content["covered_from"], content["covered_until"]) == ("25.09.2026", "27.09.2026")
    assert content["covered_day_count"] == 3
    assert (content["days_without_snapshot"], content["days_without_snapshot_count"]) == (
        "25–26.09",
        2,
    )
    assert outcome.snapshot_notes == (
        "Atingeri numărate de la 25.09: nu există snapshot CRM mai vechi",
        "Fără snapshot CRM pentru 25–26.09: atingerile din acele zile pot lipsi",
    )


def followup_frame(config: AppConfig, **overrides: Any) -> pd.DataFrame:
    rows = [
        make_snapshot_row(
            lead_id=lead_id,
            category="ACTIVE_FOLLOWUP",
            status_name="Revenire 1",
            created_at=datetime(2026, 9, 1, 12, 0, tzinfo=BUCHAREST),
            **overrides,
        )
        for lead_id in (1, 2)
    ]
    return prepare_lead_frame(rows, config)


async def test_overdue_followups_include_leads_without_data_revenire(
    app_config: AppConfig,
) -> None:
    data = tool_data(
        app_config, followup_frame(app_config), snapshot_dates=(SEPTEMBER_REPORT_DATE,)
    )

    everyone = await run_tool("overdue_followups", {"manager": ALL_MANAGERS}, data)
    dragoi = await run_tool("overdue_followups", {"manager": "Dragoi Mihaela"}, data)
    roibu = await run_tool("overdue_followups", {"manager": "Roibu Valeria"}, data)

    followup_statuses = ["Revenire 1", "Revenire 2", "Revenire 3", "Stand BY"]
    assert everyone.content["missing_followup_date"] == {
        "statuses": followup_statuses,
        "lead_count": 2,
        "manager_count": 1,
        "by_manager": [{"manager": "Dragoi Mihaela", "lead_count": 2}],
    }
    assert everyone.lead_ids == (1, 2)
    assert dragoi.content["missing_followup_date"] == {
        "statuses": followup_statuses,
        "lead_count": 2,
    }
    assert roibu.content["missing_followup_date"] == {
        "statuses": followup_statuses,
        "lead_count": 0,
    }
    assert roibu.lead_ids == ()


async def test_overdue_followups_give_no_number_when_data_revenire_is_unreadable(
    app_config: AppConfig,
) -> None:
    frame = followup_frame(app_config, data_revenire_problem="name_mismatch")
    data = tool_data(app_config, frame, snapshot_dates=(SEPTEMBER_REPORT_DATE,))

    outcome = await run_tool("overdue_followups", {"manager": ALL_MANAGERS}, data)

    assert outcome.content["missing_followup_date"] == {"unavailable": True}


async def test_overdue_followups_omit_missing_date_when_d3_param_is_off() -> None:
    raw_config = raw_repository_config()
    raw_config["modules"]["daily"]["d3"]["params"]["missing_followup_date"] = False
    config = AppConfig.model_validate(raw_config)
    data = tool_data(config, followup_frame(config), snapshot_dates=(SEPTEMBER_REPORT_DATE,))

    outcome = await run_tool("overdue_followups", {"manager": ALL_MANAGERS}, data)

    assert "missing_followup_date" not in outcome.content


async def test_untouched_leads_match_metrics(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run("untouched_leads", {}, etalon_config, lead_frame)

    untouched = untouched_leads(lead_frame, TODAY, etalon_config)
    assert content["lead_count"] == untouched.lead_count
    assert [group["lead_count"] for group in content["by_manager"]] == [
        group.lead_count for group in untouched.groups
    ]
    assert content["manager_count"] == sum(
        1 for group in untouched.groups if group.manager_name is not None
    )
    assert untouched.oldest_age_hours is not None
    assert content["oldest_age_days"] == untouched.oldest_age_hours // 24


def all_tool_calls(config: AppConfig) -> list[tuple[str, dict[str, Any]]]:
    showrooms = (ALL_SHOWROOMS, *config.status_mapping.showrooms)
    managers = [manager.name for manager in config.managers.managers]
    calls: list[tuple[str, dict[str, Any]]] = [("untouched_leads", {})]
    calls += [("overdue_followups", {"manager": name}) for name in (ALL_MANAGERS, *managers)]
    for period, showroom in product(CHAT_PERIODS, showrooms):
        calls += [
            ("funnel", {"period": period, "showroom": showroom}),
            ("loss_reasons", {"period": period, "showroom": showroom}),
            ("source_breakdown", {"period": period, "by": "source_name", "showroom": showroom}),
            ("source_breakdown", {"period": period, "by": "utm_campanie", "showroom": showroom}),
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
    calls += [("repeat_clients", {"period": period}) for period in ("luna_curenta", "luna_trecuta")]
    calls += [
        ("manager_touches", {"period": period, "manager": ALL_MANAGERS})
        for period in ("ieri", "saptamana_curenta")
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
    data = tool_data(etalon_config, lead_frame, SNAPSHOT_DATES[:2])

    outcome = await run_tool("funnel", {"period": "azi", "showroom": ALL_SHOWROOMS}, data)

    assert outcome.is_error
    assert outcome.content == {
        "error": "Nu există încă date pentru 03.06.2026: ultimul snapshot este din 02.06.2026."
    }


async def test_partial_period_states_snapshot_date(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    data = tool_data(etalon_config, lead_frame, SNAPSHOT_DATES[:2])

    outcome = await run_tool(
        "funnel", {"period": "saptamana_curenta", "showroom": ALL_SHOWROOMS}, data
    )

    assert outcome.snapshot_notes == ("Date din snapshotul din 02.06.2026",)
    assert outcome.content["data_as_of"] == "02.06.2026"
    # Неделя 01.06–03.06, снапшот за 02.06: два дня из трёх с данными.
    assert (outcome.content["day_count"], outcome.content["days_with_data"]) == (3, 2)


async def test_closed_period_with_its_snapshot_has_no_note(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    outcome = await run_tool(
        "loss_reasons",
        {"period": "luna_trecuta", "showroom": ALL_SHOWROOMS},
        tool_data(etalon_config, lead_frame),
    )

    assert outcome.snapshot_dates == (END_OF_MAY,)
    assert outcome.snapshot_notes == ()
    assert outcome.content["missing_snapshot_for"] is None


async def test_closed_period_without_its_snapshot_uses_first_later_one_with_note(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    data = tool_data(etalon_config, lead_frame, SNAPSHOT_DATES[1:])

    outcome = await run_tool(
        "loss_reasons", {"period": "luna_trecuta", "showroom": ALL_SHOWROOMS}, data
    )

    assert not outcome.is_error
    assert outcome.snapshot_dates == (date(2026, 6, 2),)
    assert outcome.snapshot_notes == (
        "Date din snapshotul din 02.06.2026 (nu există snapshot pentru 31.05.2026)",
    )
    assert outcome.content["snapshot_date"] == "02.06.2026"
    assert outcome.content["missing_snapshot_for"] == "31.05.2026"


async def test_closed_period_without_its_or_later_snapshot_has_no_data(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    data = tool_data(etalon_config, lead_frame, (date(2026, 5, 30),))

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
    assert set(TOOL_FUNCTIONS) == set(get_args(ChatToolName))


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


async def test_overdue_and_untouched_carry_lead_ids_outside_content(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    data = tool_data(etalon_config, lead_frame)

    overdue = await run_tool("overdue_followups", {"manager": ALL_MANAGERS}, data)
    one_manager = await run_tool("overdue_followups", {"manager": "Moaca Andreea"}, data)
    untouched = await run_tool("untouched_leads", {}, data)
    funnel = await run_tool("funnel", {"period": "azi", "showroom": ALL_SHOWROOMS}, data)

    expected = overdue_revenire_by_manager(lead_frame, TODAY, etalon_config)
    assert overdue.lead_ids == expected.lead_ids
    assert len(overdue.lead_ids) == expected.lead_count > 0
    moaca = [group for group in expected.groups if group.manager_name == "Moaca Andreea"]
    assert one_manager.lead_ids == (moaca[0].lead_ids if moaca else ())
    assert untouched.lead_ids == untouched_leads(lead_frame, TODAY, etalon_config).lead_ids
    assert funnel.lead_ids == ()
    for outcome in (overdue, one_manager, untouched):
        serialized = json.dumps(outcome.content)
        assert not any(str(lead_id) in serialized for lead_id in outcome.lead_ids)


ALL_COMPANY = {"showroom": ALL_SHOWROOMS}


@pytest.mark.parametrize(
    ("period", "message"),
    [
        ({"day": "2026-06-04"}, "data 04.06.2026 este în viitor"),
        ({"year": 2026, "month": 7}, "luna 07.2026 este în viitor"),
        ({"day": "2025-05-30"}, "nu există date înainte de 31.05.2025"),
        ({"year": 2025, "month": 4}, "nu există date înainte de 05.2025"),
        ({"year": 2026, "month": 13}, "less than or equal to 12"),
        ({"day": "2026-05-15", "year": 2026}, "Extra inputs"),
    ],
)
async def test_specific_date_out_of_range_is_tool_error(
    etalon_config: AppConfig, lead_frame: pd.DataFrame, period: dict[str, Any], message: str
) -> None:
    outcome = await run_tool(
        "funnel", {"period": period, **ALL_COMPANY}, tool_data(etalon_config, lead_frame)
    )

    assert outcome.is_error
    assert message in outcome.content["error"]


async def test_first_allowed_month_is_accepted(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run(
        "funnel", {"period": {"year": 2025, "month": 5}, **ALL_COMPANY}, etalon_config, lead_frame
    )

    assert content["period"] == "mai 2025"


async def test_today_as_specific_day_is_azi(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    outcome = await run_tool(
        "funnel",
        {"period": {"day": "2026-06-03"}, **ALL_COMPANY},
        tool_data(etalon_config, lead_frame),
    )

    assert outcome.content["period"] == "azi"
    assert outcome.scope == "Perioada: 03.06.2026 (azi)"


async def test_current_month_is_luna_curenta(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    content = await run(
        "funnel", {"period": {"year": 2026, "month": 6}, **ALL_COMPANY}, etalon_config, lead_frame
    )

    assert content["period"] == "luna_curenta"


async def test_funnel_for_specific_day_matches_metrics(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    outcome = await run_tool(
        "funnel",
        {"period": {"day": "2026-05-15"}, **ALL_COMPANY},
        tool_data(etalon_config, lead_frame),
    )

    window = daily_window(date(2026, 5, 15), etalon_config.status_mapping.time)
    counts = lead_counts(lead_frame, window, END_OF_MAY, etalon_config)
    assert counts.leads > 0
    assert outcome.content["counts"] == {name: getattr(counts, name) for name in COUNT_NAMES}
    assert outcome.content["period"] == "15.05.2026"
    assert outcome.scope == "Perioada: 15.05.2026"
    assert outcome.snapshot_notes == (
        "Date din snapshotul din 31.05.2026 (nu există snapshot pentru 15.05.2026)",
    )


async def test_specific_month_matches_last_month(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    by_month = await run_tool(
        "funnel",
        {"period": {"year": 2026, "month": 5}, **ALL_COMPANY},
        tool_data(etalon_config, lead_frame),
    )
    by_name = await run(
        "funnel", {"period": "luna_trecuta", **ALL_COMPANY}, etalon_config, lead_frame
    )

    assert by_name == {**by_month.content, "period": "luna_trecuta"}
    assert by_month.content["period"] == "mai 2026"
    assert by_month.scope == "Perioada: mai 2026"


async def test_compare_periods_accepts_day_and_month(
    etalon_config: AppConfig, lead_frame: pd.DataFrame
) -> None:
    outcome = await run_tool(
        "compare_periods",
        {
            "metric": "leads",
            "period_a": {"day": "2026-05-15"},
            "period_b": {"year": 2026, "month": 5},
            **ALL_COMPANY,
        },
        tool_data(etalon_config, lead_frame),
    )

    assert not outcome.is_error, outcome.content
    assert outcome.scope == "Perioada: 15.05.2026 vs mai 2026"


def test_tool_definitions_period_enums_per_tool(app_config: AppConfig) -> None:
    definitions = {definition["name"]: definition for definition in tool_definitions(app_config)}

    def period_forms(tool: str) -> list[Any]:
        schema: Any = definitions[tool]["input_schema"]
        return cast(list[Any], schema["properties"]["period"]["anyOf"])

    named, day = period_forms("manager_touches")
    assert named["enum"] == ["azi", "ieri", "saptamana_curenta", "saptamana_trecuta"]
    assert set(day["properties"]) == {"day"}
    named, month = period_forms("repeat_clients")
    assert named["enum"] == ["luna_curenta", "luna_trecuta"]
    assert set(month["properties"]) == {"year", "month"}
    assert len(period_forms("source_breakdown")) == 3


def test_period_schema_is_anyof_of_enum_day_and_month(app_config: AppConfig) -> None:
    definitions = {definition["name"]: definition for definition in tool_definitions(app_config)}
    properties: Any = definitions["compare_periods"]["input_schema"]["properties"]

    for name in ("period_a", "period_b"):
        named, day, month = properties[name]["anyOf"]
        assert named["enum"] == list(CHAT_PERIODS)
        assert day["properties"] == {"day": {"type": "string", "format": "date"}}
        assert month["properties"]["month"]["enum"] == list(range(1, 13))
        for variant in (day, month):
            assert variant["required"] == list(variant["properties"])
            assert variant["additionalProperties"] is False


def test_compared_periods_are_described_as_evaluated_and_base(etalon_config: AppConfig) -> None:
    [compare] = [
        tool for tool in tool_definitions(etalon_config) if tool["name"] == "compare_periods"
    ]
    properties = cast(dict[str, dict[str, str]], dict(compare["input_schema"])["properties"])

    assert properties["period_a"]["description"].startswith("Perioada evaluată")
    assert properties["period_b"]["description"].startswith("Baza comparației")
    assert "period_a este perioada evaluată" in compare["description"]
