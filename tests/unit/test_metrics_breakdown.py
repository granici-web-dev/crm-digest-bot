from dataclasses import asdict
from datetime import date, datetime
from typing import Any

import pandas as pd
import pytest

from digest.config import AppConfig
from digest.metrics.breakdown import BreakdownColumn, lead_breakdown, lead_counts_by_column
from digest.metrics.frame import prepare_lead_frame
from digest.metrics.kpi import Period, add_counts, kpis_from, lead_counts
from factories import BUCHAREST, etalon_lead_rows, load_etalon, make_snapshot_row

# Окно и дата анализа эталона, как в test_kpi_matches_etalon.
ETALON_PERIOD = Period(
    start=datetime(2026, 5, 1, tzinfo=BUCHAREST), end=datetime(2026, 6, 17, tzinfo=BUCHAREST)
)
ETALON_ANALYSIS_DATE = date(2026, 6, 27)
SEPTEMBER = Period(
    start=datetime(2026, 9, 1, tzinfo=BUCHAREST), end=datetime(2026, 10, 1, tzinfo=BUCHAREST)
)
IN_SEPTEMBER = datetime(2026, 9, 10, 12, 0, tzinfo=BUCHAREST)
SEPTEMBER_END = date(2026, 9, 30)


def etalon_frame(app_config: AppConfig) -> pd.DataFrame:
    return prepare_lead_frame(
        etalon_lead_rows(load_etalon(), app_config.status_mapping), app_config
    )


def september_frame(app_config: AppConfig, *overrides: dict[str, Any]) -> pd.DataFrame:
    rows = [
        make_snapshot_row(lead_id=lead_id, created_at=IN_SEPTEMBER, **override)
        for lead_id, override in enumerate(overrides, start=1)
    ]
    return prepare_lead_frame(rows, app_config)


@pytest.mark.parametrize(
    ("column", "etalon_key"),
    [("source_name", "expected_by_source"), ("utm_campanie", "expected_by_utm_campaign")],
    ids=["source", "campaign"],
)
def test_counts_by_column_match_etalon(
    app_config: AppConfig, column: BreakdownColumn, etalon_key: str
) -> None:
    counts_by_key = lead_counts_by_column(
        etalon_frame(app_config), column, ETALON_PERIOD, ETALON_ANALYSIS_DATE, app_config
    )

    expected_by_key = {entry["key"]: entry for entry in load_etalon()[etalon_key]}
    assert set(counts_by_key) == set(expected_by_key)
    for key, expected in expected_by_key.items():
        assert asdict(counts_by_key[key]) == expected["counts"], key
        assert asdict(kpis_from(counts_by_key[key])) == pytest.approx(
            expected["kpi"], abs=1e-9
        ), key


@pytest.mark.parametrize("column", ["source_name", "utm_campanie"])
def test_breakdown_rows_sum_to_company_counts(
    app_config: AppConfig, column: BreakdownColumn
) -> None:
    lead_frame = etalon_frame(app_config)

    breakdown = lead_breakdown(
        lead_frame, column, ETALON_PERIOD, ETALON_ANALYSIS_DATE, app_config, min_leads=10
    )

    parts = [row.counts for row in breakdown.rows]
    parts += [row.counts for row in (breakdown.other, breakdown.without_key) if row is not None]
    company = lead_counts(lead_frame, ETALON_PERIOD, ETALON_ANALYSIS_DATE, app_config)
    assert add_counts(parts) == company
    assert breakdown.total.counts == company


def test_sources_below_threshold_collapse_into_other_with_summed_counts(
    app_config: AppConfig,
) -> None:
    breakdown = lead_breakdown(
        etalon_frame(app_config),
        "source_name",
        ETALON_PERIOD,
        ETALON_ANALYSIS_DATE,
        app_config,
        min_leads=10,
    )

    assert [row.key for row in breakdown.rows] == [
        "Showroom",
        "Telefon",
        "Site",
        "WhatsApp",
        "Mail",
    ]
    assert breakdown.other_keys == (
        "Arhirtect",
        "Client Fidel",
        "Colaborare",
        "Meta ADS",
        "Recomandare",
    )
    assert breakdown.other is not None
    assert breakdown.other.key is None
    assert (breakdown.other.counts.leads, breakdown.other.counts.clienti) == (8, 2)
    assert breakdown.without_key is None


def test_lead_without_source_is_never_collapsed(app_config: AppConfig) -> None:
    lead_frame = september_frame(
        app_config, {"source_name": None}, {"source_name": "Site"}, {"source_name": "Site"}
    )

    breakdown = lead_breakdown(
        lead_frame, "source_name", SEPTEMBER, SEPTEMBER_END, app_config, min_leads=5
    )

    assert breakdown.rows == ()
    assert breakdown.other_keys == ("Site",)
    assert breakdown.without_key is not None
    assert breakdown.without_key.counts.leads == 1


def test_rows_sorted_by_leads_then_name(app_config: AppConfig) -> None:
    lead_frame = september_frame(
        app_config,
        {"source_name": "WhatsApp"},
        {"source_name": "Telefon"},
        {"source_name": "Site"},
        {"source_name": "Site"},
    )

    breakdown = lead_breakdown(
        lead_frame, "source_name", SEPTEMBER, SEPTEMBER_END, app_config, min_leads=1
    )

    assert [row.key for row in breakdown.rows] == ["Site", "Telefon", "WhatsApp"]
    assert breakdown.other is None


def test_partnership_is_excluded_from_breakdown(app_config: AppConfig) -> None:
    lead_frame = september_frame(
        app_config, {"source_name": "Colaborare", "category": "PARTNERSHIP"}, {}
    )

    counts_by_key = lead_counts_by_column(
        lead_frame, "source_name", SEPTEMBER, SEPTEMBER_END, app_config
    )

    assert set(counts_by_key) == {"Site"}
