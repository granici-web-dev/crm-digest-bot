from dataclasses import replace
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Any

import pandas as pd
import pytest
from openpyxl import load_workbook
from syrupy.assertion import SnapshotAssertion

from digest.config import AppConfig
from digest.metrics.daily import PreviousSnapshot
from digest.metrics.frame import prepare_lead_frame
from digest.metrics.weekly import weekly_lead_tables
from digest.reports.context import ReportContext
from digest.reports.modules import IMPLEMENTED_MODULES
from digest.reports.modules.weekly import (
    day_detail_table,
    day_showroom_table,
    showroom_source_table,
    week_range_label,
)
from digest.reports.render import change_label, percent, signed_percent_one_decimal
from factories import BUCHAREST, make_lead_links, make_snapshot_row, raw_repository_config

SUNDAY = date(2026, 9, 27)
MONDAY = date(2026, 9, 21)
WEEKLY_TEXT_MODULES = ("w1", "w2", "w3", "w4", "w6", "w8", "w12")


def at(day: date, hour: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, tzinfo=BUCHAREST)


def lead(lead_id: int, created_at: datetime, **overrides: Any) -> dict[str, Any]:
    return make_snapshot_row(
        **{"lead_id": lead_id, "created_at": created_at, "last_contact_at": created_at, **overrides}
    )


def week_frame(app_config: AppConfig) -> pd.DataFrame:
    old = at(date(2026, 8, 3), 11)
    rows = [
        lead(1, at(MONDAY, 11), showroom="Brașov", source_name="Site", ofertat=True),
        lead(2, at(MONDAY, 20), showroom="București", source_name="WhatsApp"),
        lead(3, at(SUNDAY, 12), showroom="Cluj", source_name="Telefon"),
        lead(4, at(SUNDAY, 13), showroom=None, source_name="Site"),
        lead(5, at(MONDAY, 12), showroom="Brașov", source_name="Showroom"),
        lead(
            6,
            at(MONDAY - timedelta(days=6), 11),
            category="WON",
            status_name="Clienți",
            ofertat=True,
            converted_at=at(SUNDAY, 16),
        ),
        lead(
            7,
            old,
            category="LOST",
            loss_reason="BUGET",
            status_name="BUGET",
            status_changed_at=at(MONDAY, 15),
            showroom="Cluj",
        ),
        lead(
            8,
            at(SUNDAY, 14),
            category="LOST",
            loss_reason="NU_RASPUNS",
            status_name="NU A RASPUNS",
            showroom="Cluj",
        ),
    ]
    return prepare_lead_frame(rows, app_config)


def context(app_config: AppConfig, lead_frame: pd.DataFrame) -> ReportContext:
    week_ago = PreviousSnapshot(SUNDAY - timedelta(days=7), lead_frame[lead_frame["lead_id"].eq(6)])
    return ReportContext(
        SUNDAY,
        None,
        week_ago,
        week_ago,
        None,
        app_config,
        "sofabelle",
        make_lead_links(app_config.status_mapping),
    )


@pytest.mark.parametrize("module_id", WEEKLY_TEXT_MODULES)
def test_weekly_module_text(
    app_config: AppConfig, module_id: str, snapshot: SnapshotAssertion
) -> None:
    lead_frame = week_frame(app_config)

    result = IMPLEMENTED_MODULES[module_id](lead_frame, context(app_config, lead_frame))

    assert result.text == snapshot


def test_showroom_visits_text_counts_reveniri(
    app_config: AppConfig, snapshot: SnapshotAssertion
) -> None:
    phone_key, email_key = "a" * 64, "b" * 64
    old = at(date(2026, 8, 3), 11)
    rows = [
        lead(1, old, contact_phone_key=phone_key),
        lead(2, old, contact_email_key=email_key),
        lead(3, at(MONDAY, 12), showroom="Brașov", source_name="Showroom"),
        lead(4, at(SUNDAY, 12), source_name="Showroom", contact_phone_key=phone_key),
        lead(5, at(SUNDAY, 13), source_name="Showroom", contact_email_key=email_key),
    ]
    lead_frame = prepare_lead_frame(rows, app_config)

    result = IMPLEMENTED_MODULES["w2"](lead_frame, context(app_config, lead_frame))

    assert "Reveniri: 2" in result.text
    assert result.text == snapshot


def test_showroom_visits_text_hides_zero_reveniri(app_config: AppConfig) -> None:
    # mefi не фиксирует повторный приход (ADR-007): ноль здесь значит «нет данных», а не
    # «никто не вернулся», поэтому строки нет.
    lead_frame = week_frame(app_config)

    result = IMPLEMENTED_MODULES["w2"](lead_frame, context(app_config, lead_frame))

    assert "Reveniri" not in result.text


def test_day_showroom_table_matches_manual_report_layout(app_config: AppConfig) -> None:
    tables = weekly_lead_tables(week_frame(app_config), SUNDAY, app_config)

    table = day_showroom_table(tables.by_day_showroom, "Zi lucrătoare")

    assert table[0] == ["Zi lucrătoare", "Brașov", "București", "Cluj", "(fără showroom)", "TOTAL"]
    assert table[1] == ["21-09 Luni", 1, 0, 0, 0, 1]
    assert table[2] == ["22-09 Marți", 0, 1, 0, 0, 1]
    assert table[-1] == ["TOTAL", 1, 1, 2, 1, 5]
    assert len(table) == 1 + 7 + 1


def test_showroom_source_table_has_without_source_column_and_totals(
    app_config: AppConfig,
) -> None:
    tables = weekly_lead_tables(week_frame(app_config), SUNDAY, app_config)

    table = showroom_source_table(tables, "Fără sursă")

    assert table[0] == ["Showroom", "Site", "Telefon", "WhatsApp", "Fără sursă", "TOTAL"]
    assert table[1] == ["Brașov", 1, 0, 0, 0, 1]
    assert table[4] == ["(fără showroom)", 1, 0, 0, 0, 1]
    assert table[-1] == ["TOTAL", 3, 1, 1, 0, 5]


def test_day_detail_table_closes_each_day_with_total(app_config: AppConfig) -> None:
    tables = weekly_lead_tables(week_frame(app_config), SUNDAY, app_config)

    table = day_detail_table(tables, "Fără sursă")

    assert table[1:4] == [
        ["21-09-2026  Luni"],
        ["   Brașov", 1, 0, 0, 0, 1],
        ["Total zi 21-09", 1, 0, 0, 0, 1],
    ]
    assert table[-1] == ["Total zi 27-09", 2, 1, 0, 0, 3]


def test_week_range_label_spans_months() -> None:
    assert week_range_label((date(2026, 9, 21), date(2026, 9, 27))) == "21–27.09"
    assert week_range_label((date(2026, 9, 28), date(2026, 10, 4))) == "28.09–04.10"


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "(—)"), (0.0, "(=)"), (0.12, "(+12%)"), (-0.08, "(−8%)")],
)
def test_change_label(value: float | None, expected: str) -> None:
    assert change_label(value) == expected


def test_percent_of_unknown_ratio_is_dash() -> None:
    assert (percent(None), percent(0.456)) == ("—", "46%")


def test_weekly_texts_follow_working_hours_sources_and_reason_labels_from_config() -> None:
    raw = raw_repository_config()
    raw["status_mapping"]["time"]["working_hours"] = {"start": "09:00", "end": "18:00"}
    raw["status_mapping"]["sources"]["showroom_visit"] = ["Showroom", "Vizita"]
    raw["status_mapping"]["categories"]["LOST"]["reasons"]["BUGET"]["label"] = "Preț"
    config = AppConfig.model_validate(raw)
    lead_frame = week_frame(config)
    report_context = context(config, lead_frame)

    leads_text = IMPLEMENTED_MODULES["w1"](lead_frame, report_context).text
    losses_text = IMPLEMENTED_MODULES["w4"](lead_frame, report_context).text
    document = IMPLEMENTED_MODULES["w12"](lead_frame, report_context).document

    assert "Ore de lucru 09:00–18:00" in leads_text
    assert "fără Showroom, Vizita:" in leads_text
    assert "Preț 1" in losses_text
    assert document is not None
    summary = load_workbook(BytesIO(document.content))["Zi × showroom"]
    assert "(fără Sursa=Showroom, Vizita)" in str(summary["A1"].value)
    assert "ore de lucru 09:00–18:00" in str(summary["A2"].value)


def test_funnel_and_week_over_week_numbers_on_render_path(app_config: AppConfig) -> None:
    lead_frame = week_frame(app_config)
    report_context = context(app_config, lead_frame)

    funnel_text = IMPLEMENTED_MODULES["w3"](lead_frame, report_context).text
    change_text = IMPLEMENTED_MODULES["w8"](lead_frame, report_context).text

    # Лиды 1–5 и 8 созданы в окне недели, из них оферта только у лида 1.
    assert (
        "Lead-uri (toate sursele, inclusiv vizite) 6 → Utile 6 (irelevante 0) → Oferte 1 "
        "→ Clienți 0" in funnel_text
    )
    assert "Lead→Ofertă 17% · Ofertă→Client 0%" in funnel_text
    # Лиды недели 1–4 и 8 против лида 6 прошлой недели; визит 5; контракт лида 6 в воскресенье;
    # оферта лида 1 новая к снапшоту недельной давности, где был только лид 6.
    assert "Lead-uri 5 (+400%)" in change_text
    assert "Vizite 1 (—)" in change_text
    assert "Oferte 1" in change_text
    assert "Contracte (după data semnării) 1 (—)" in change_text


@pytest.mark.parametrize(
    ("value", "expected"), [(0.174, "+17,4%"), (-0.15, "−15,0%"), (0.0, "0,0%")]
)
def test_signed_percent_one_decimal(value: float, expected: str) -> None:
    assert signed_percent_one_decimal(value) == expected


def irrelevant_week_frame(app_config: AppConfig, day: date) -> pd.DataFrame:
    rows = [
        *(lead(lead_id, at(day, 12)) for lead_id in range(1, 8)),
        *(
            lead(lead_id, at(day, 12), category="LOST", loss_reason="IRELEVANT")
            for lead_id in range(8, 11)
        ),
        *(
            lead(lead_id, at(day, 12), source_name="Telefon", utm_campanie="BZA <03>")
            for lead_id in range(11, 16)
        ),
    ]
    return prepare_lead_frame(rows, app_config)


def test_irr_by_campaign_text_notes_substituted_previous_week_snapshot(
    app_config: AppConfig, snapshot: SnapshotAssertion
) -> None:
    lead_frame = irrelevant_week_frame(app_config, SUNDAY)
    substitute_date = SUNDAY - timedelta(days=5)
    previous = PreviousSnapshot(
        substitute_date, irrelevant_week_frame(app_config, SUNDAY - timedelta(days=7))
    )
    report_context = replace(context(app_config, lead_frame), previous_week=previous)

    text = IMPLEMENTED_MODULES["w6"](lead_frame, report_context).text

    assert "Săpt. trecută: după snapshotul din 22.09 (lipsește cel din 20.09)" in text
    assert "BZA &lt;03&gt;" in text
    assert text == snapshot


def test_irr_by_campaign_without_previous_week_shows_dash(app_config: AppConfig) -> None:
    lead_frame = irrelevant_week_frame(app_config, SUNDAY)
    report_context = replace(context(app_config, lead_frame), previous_week=None)

    text = IMPLEMENTED_MODULES["w6"](lead_frame, report_context).text

    assert "total 20,0% (săpt. trecută —), țintă ≤20,0%" in text
    assert "Site 30,0% (3 din 10) ✗, săpt. trecută —" in text
    assert "Săpt. trecută: fără snapshot" in text


def test_irr_by_campaign_flattens_and_cuts_long_campaign(app_config: AppConfig) -> None:
    rows = [
        lead(lead_id, at(SUNDAY, 12), utm_campanie="BZA\n" + "x" * 100) for lead_id in range(1, 6)
    ]
    lead_frame = prepare_lead_frame(rows, app_config)
    report_context = replace(context(app_config, lead_frame), previous_week=None)

    text = IMPLEMENTED_MODULES["w6"](lead_frame, report_context).text

    assert "„BZA " + "x" * 35 + "…” 0,0% (0 din 5)" in text
