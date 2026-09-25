from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from digest.config import AppConfig
from digest.metrics.daily import PreviousSnapshot, daily_window
from digest.metrics.frame import prepare_lead_frame
from digest.metrics.weekly import (
    LEAD_ROW_COLUMNS,
    daily_window_days,
    relative_change,
    week_over_week,
    weekly_funnel,
    weekly_lead_rows,
    weekly_lead_tables,
    weekly_loss_reasons,
    weekly_showroom_visit_rows,
    weekly_showroom_visits,
    working_days,
)
from factories import BUCHAREST, make_snapshot_row

SUNDAY = date(2026, 9, 27)
MONDAY = date(2026, 9, 21)
UTC = ZoneInfo("UTC")


def at(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=BUCHAREST)


def frame(app_config: AppConfig, *rows: dict[str, Any]) -> pd.DataFrame:
    return prepare_lead_frame(list(rows), app_config)


def lead(lead_id: int, created_at: datetime, **overrides: Any) -> dict[str, Any]:
    return make_snapshot_row(
        **{"lead_id": lead_id, "created_at": created_at, "last_contact_at": created_at, **overrides}
    )


def visit(lead_id: int, created_at: datetime, **overrides: Any) -> dict[str, Any]:
    return lead(lead_id, created_at, source_name="Showroom", **overrides)


def lost(lead_id: int, created_at: datetime, reason: str, **overrides: Any) -> dict[str, Any]:
    return lead(lead_id, created_at, category="LOST", loss_reason=reason, **overrides)


@pytest.mark.parametrize(
    ("hour", "minute", "second", "expected_day"),
    [
        (9, 59, 59, date(2026, 9, 24)),
        (10, 0, 0, date(2026, 9, 23)),
        (18, 59, 59, date(2026, 9, 23)),
        (19, 0, 0, date(2026, 9, 24)),
        (0, 30, 0, date(2026, 9, 24)),
    ],
)
def test_working_day_keeps_10_to_19_and_moves_everything_else_to_next_day(
    app_config: AppConfig, hour: int, minute: int, second: int, expected_day: date
) -> None:
    created_at = pd.Series([at(date(2026, 9, 23), hour, minute, second)]).dt.tz_convert(BUCHAREST)

    [day] = working_days(created_at, app_config.status_mapping.time)

    assert day == expected_day


def test_week_is_leads_with_working_day_monday_to_sunday(app_config: AppConfig) -> None:
    previous_sunday = SUNDAY - timedelta(days=7)
    leads = frame(
        app_config,
        lead(1, at(previous_sunday, 9, 59)),
        lead(2, at(previous_sunday, 19, 0)),
        lead(3, at(previous_sunday, 18, 59)),
        lead(4, at(SUNDAY, 9, 59)),
        lead(5, at(SUNDAY, 18, 59, 59)),
        lead(6, at(SUNDAY, 19, 0)),
    )

    tables = weekly_lead_tables(leads, SUNDAY, app_config)

    assert tables.by_day_showroom.day_total(MONDAY) == 2
    assert tables.by_day_showroom.day_total(SUNDAY) == 1
    assert tables.total == 3


def test_week_on_dst_end_reads_local_time(app_config: AppConfig) -> None:
    dst_sunday = date(2026, 10, 25)
    leads = frame(
        app_config,
        # 03:30 по Бухаресту бывает дважды: 00:30 UTC (летнее время) и 01:30 UTC (зимнее).
        lead(1, datetime(2026, 10, 25, 0, 30, tzinfo=UTC)),
        lead(2, datetime(2026, 10, 25, 1, 30, tzinfo=UTC)),
        lead(3, datetime(2026, 10, 25, 16, 59, tzinfo=UTC)),
        lead(4, datetime(2026, 10, 25, 17, 0, tzinfo=UTC)),
    )

    tables = weekly_lead_tables(leads, dst_sunday, app_config)

    assert tables.by_day_showroom.day_total(dst_sunday) == 1
    assert tables.total == 1


def test_showroom_source_is_excluded_from_lead_tables(app_config: AppConfig) -> None:
    leads = frame(app_config, lead(1, at(SUNDAY, 11)), visit(2, at(SUNDAY, 11)))

    tables = weekly_lead_tables(leads, SUNDAY, app_config)

    assert tables.total == 1
    assert "Showroom" not in tables.sources


def test_leads_without_showroom_or_source_get_their_own_columns(app_config: AppConfig) -> None:
    leads = frame(
        app_config,
        lead(1, at(SUNDAY, 11), showroom=None),
        lead(2, at(SUNDAY, 11), source_name=None, showroom="Cluj"),
    )

    tables = weekly_lead_tables(leads, SUNDAY, app_config)

    assert tables.by_day_showroom.showrooms == ("Brașov", "București", "Cluj", None)
    assert tables.by_day_showroom.counts[SUNDAY] == {
        "Brașov": 0,
        "București": 0,
        "Cluj": 1,
        None: 1,
    }
    assert tables.by_showroom_source[None] == {"Site": 1, None: 0}
    assert tables.by_showroom_source["Cluj"] == {"Site": 0, None: 1}


def test_source_columns_go_by_weekly_total_then_alphabet(app_config: AppConfig) -> None:
    sources = ["Site", "Site", "Site", "WhatsApp", "Telefon", "WhatsApp", "Telefon", "Mail"]
    leads = frame(
        app_config,
        *(
            lead(lead_id, at(SUNDAY, 11), source_name=source)
            for lead_id, source in enumerate(sources)
        ),
    )

    tables = weekly_lead_tables(leads, SUNDAY, app_config)

    assert tables.sources == ("Site", "Telefon", "WhatsApp", "Mail", None)


def test_day_detail_lists_only_showrooms_with_leads_and_totals_agree(
    app_config: AppConfig,
) -> None:
    leads = frame(
        app_config,
        lead(1, at(MONDAY, 11), showroom="Brașov", source_name="Site"),
        lead(2, at(MONDAY, 12), showroom="Cluj", source_name="Telefon"),
        lead(3, at(SUNDAY, 11), showroom="Cluj", source_name="Site"),
    )

    tables = weekly_lead_tables(leads, SUNDAY, app_config)

    assert list(tables.by_day_showroom_source[MONDAY]) == ["Brașov", "Cluj"]
    assert tables.by_day_showroom_source[MONDAY]["Cluj"] == {"Site": 0, "Telefon": 1, None: 0}
    assert tables.by_day_showroom_source[MONDAY + timedelta(days=1)] == {}
    assert tables.by_showroom_source["Cluj"] == {"Site": 1, "Telefon": 1, None: 0}
    assert sum(tables.source_total(source) for source in tables.sources) == 3
    assert tables.by_day_showroom.showroom_total("Cluj") == 2


def test_unconfigured_showroom_gets_its_own_column(app_config: AppConfig) -> None:
    leads = frame(app_config, lead(1, at(SUNDAY, 11), showroom="Iași"))

    tables = weekly_lead_tables(leads, SUNDAY, app_config)

    assert tables.by_day_showroom.showrooms == ("Brașov", "București", "Cluj", "Iași", None)
    assert tables.by_day_showroom.counts[SUNDAY]["Iași"] == 1


def test_showroom_visits_use_daily_windows_of_the_week(app_config: AppConfig) -> None:
    saturday = SUNDAY - timedelta(days=1)
    previous_sunday = SUNDAY - timedelta(days=7)
    leads = frame(
        app_config,
        visit(1, at(previous_sunday, 19, 0), showroom="Brașov"),
        visit(2, at(previous_sunday, 18, 59), showroom="Brașov"),
        visit(3, at(saturday, 19, 30), showroom="Cluj"),
        visit(4, at(SUNDAY, 9, 0), showroom="Cluj"),
        visit(5, at(SUNDAY, 19, 0), showroom="Cluj"),
        lead(6, at(SUNDAY, 11), showroom="Cluj"),
    )

    visits = weekly_showroom_visits(leads, SUNDAY, app_config)

    assert visits.counts[MONDAY]["Brașov"] == 1
    assert visits.counts[SUNDAY]["Cluj"] == 2
    assert visits.total == 3


def test_funnel_counts_leads_created_in_week_window(app_config: AppConfig) -> None:
    leads = frame(
        app_config,
        lead(1, at(MONDAY, 11), ofertat=True),
        lead(2, at(MONDAY, 12), category="WON", status_name="Clienți", ofertat=True),
        lost(3, at(SUNDAY, 18), "IRELEVANT", status_name="IRELEVANT"),
        lead(4, at(SUNDAY, 12), category="PARTNERSHIP", status_name="DESIGNER"),
        lead(5, at(SUNDAY, 19, 0)),
        lead(6, at(MONDAY - timedelta(days=1), 18, 59)),
    )

    funnel = weekly_funnel(leads, SUNDAY, app_config)

    counts = funnel.counts
    assert (counts.leads, counts.useful, counts.offers, counts.clienti) == (3, 2, 2, 1)
    assert funnel.kpis.l2o == 1.0
    assert funnel.kpis.o2c == 0.5


def test_loss_reasons_count_status_changes_in_week_and_leads_created_lost(
    app_config: AppConfig,
) -> None:
    old = at(date(2026, 8, 1), 11)
    leads = frame(
        app_config,
        lost(1, old, "BUGET", status_changed_at=at(MONDAY, 11), showroom="Brașov"),
        lost(2, old, "BUGET", status_changed_at=at(SUNDAY, 18), showroom="Cluj"),
        lost(3, at(SUNDAY, 12), "NU_RASPUNS", showroom="Cluj"),
        lost(4, old, "TIMP", status_changed_at=at(SUNDAY, 19, 0), showroom="Cluj"),
        lost(5, old, "TIMP", status_changed_at=None, showroom="Cluj"),
        lead(6, old, status_changed_at=at(MONDAY, 12), showroom="Cluj"),
        lost(7, old, "CONCURENT", status_changed_at=at(MONDAY, 13), showroom=None),
    )

    losses = weekly_loss_reasons(leads, SUNDAY, app_config)

    assert losses.reasons == tuple(app_config.status_mapping.categories.LOST.reasons)
    assert losses.by_showroom["Cluj"]["BUGET"] == 1
    assert losses.by_showroom["Cluj"]["NU_RASPUNS"] == 1
    assert losses.by_showroom[None]["CONCURENT"] == 1
    assert losses.reason_total("BUGET") == 2
    assert losses.reason_total("TIMP") == 0
    assert losses.total == 4


def week_over_week_leads(app_config: AppConfig) -> pd.DataFrame:
    previous_monday = MONDAY - timedelta(days=7)
    return frame(
        app_config,
        lead(1, at(MONDAY, 11), ofertat=True),
        lead(2, at(MONDAY, 12), ofertat=False),
        lead(3, at(previous_monday, 11), ofertat=True),
        visit(4, at(SUNDAY, 12)),
        visit(5, at(previous_monday, 12)),
        visit(6, at(previous_monday, 13)),
        lead(7, at(date(2026, 8, 1), 11), converted_at=at(SUNDAY, 17), ofertat=True),
    )


def test_week_over_week_counts_both_weeks_from_sundays_snapshot(app_config: AppConfig) -> None:
    change = week_over_week(week_over_week_leads(app_config), None, SUNDAY, app_config)

    assert (change.leads, change.leads_previous) == (2, 1)
    assert (change.showroom_visits, change.showroom_visits_previous) == (1, 2)
    assert (change.contracts, change.contracts_previous) == (1, 0)
    assert change.offers is None


def test_week_over_week_offers_are_diff_with_week_ago_snapshot(app_config: AppConfig) -> None:
    today = week_over_week_leads(app_config)
    week_ago_frame = frame(
        app_config,
        lead(3, at(MONDAY - timedelta(days=7), 11), ofertat=True),
        lead(7, at(date(2026, 8, 1), 11), ofertat=False),
    )

    change = week_over_week(
        today, PreviousSnapshot(SUNDAY - timedelta(days=7), week_ago_frame), SUNDAY, app_config
    )

    assert change.offers == 2


@pytest.mark.parametrize(
    ("current", "previous", "expected"),
    [(12, 10, 0.2), (8, 10, -0.2), (4, 4, 0.0), (0, 0, None), (3, 0, None)],
)
def test_relative_change(current: int, previous: int, expected: float | None) -> None:
    assert relative_change(current, previous) == pytest.approx(expected)


def test_lead_rows_are_week_leads_sorted_by_day_and_time(app_config: AppConfig) -> None:
    leads = frame(
        app_config,
        lead(1, at(MONDAY, 11)),
        lead(2, at(SUNDAY, 11)),
        lead(3, at(MONDAY, 12)),
        visit(4, at(SUNDAY, 12)),
        lead(5, at(SUNDAY, 19)),
    )

    rows = weekly_lead_rows(leads, SUNDAY, app_config)
    visit_rows = weekly_showroom_visit_rows(leads, SUNDAY, app_config)

    assert tuple(rows.columns) == LEAD_ROW_COLUMNS
    assert list(rows["lead_id"]) == [1, 3, 2]
    assert len(rows) == weekly_lead_tables(leads, SUNDAY, app_config).total
    assert list(rows["day"]) == [MONDAY, MONDAY, SUNDAY]
    assert list(visit_rows["lead_id"]) == [4]
    assert tuple(visit_rows.columns) == LEAD_ROW_COLUMNS


@pytest.mark.parametrize(
    "created_at",
    [
        at(SUNDAY, 18, 59, 59),
        at(SUNDAY, 19, 0),
        at(SUNDAY, 0, 30),
        datetime(2026, 10, 25, 0, 30, tzinfo=UTC),
        datetime(2026, 10, 25, 16, 59, tzinfo=UTC),
        datetime(2026, 3, 29, 0, 30, tzinfo=UTC),
        datetime(2026, 3, 29, 15, 59, tzinfo=UTC),
        datetime(2026, 3, 29, 16, 0, tzinfo=UTC),
    ],
)
def test_visit_day_is_the_daily_window_containing_the_lead(
    app_config: AppConfig, created_at: datetime
) -> None:
    time_settings = app_config.status_mapping.time
    created_at_series = pd.Series([created_at]).dt.tz_convert(BUCHAREST)

    [day] = daily_window_days(created_at_series, time_settings)

    window = daily_window(day, time_settings)
    assert window.start <= created_at < window.end


def test_week_on_dst_start_reads_local_time(app_config: AppConfig) -> None:
    dst_sunday = date(2026, 3, 29)
    leads = frame(
        app_config,
        # 29.03.2026 в 03:00 часы переводятся на 04:00: 15:59 UTC это 18:59 по Бухаресту.
        lead(1, datetime(2026, 3, 29, 15, 59, tzinfo=UTC)),
        lead(2, datetime(2026, 3, 29, 16, 0, tzinfo=UTC)),
        lead(3, datetime(2026, 3, 29, 6, 59, tzinfo=UTC)),
        lead(4, datetime(2026, 3, 29, 7, 0, tzinfo=UTC)),
    )

    tables = weekly_lead_tables(leads, dst_sunday, app_config)

    assert tables.by_day_showroom.day_total(dst_sunday) == 2
    assert tables.total == 2


@pytest.mark.parametrize(
    ("dst_sunday", "last_in_window", "first_after_window"),
    [
        (
            date(2026, 3, 29),
            datetime(2026, 3, 29, 15, 59, tzinfo=UTC),
            datetime(2026, 3, 29, 16, 0, tzinfo=UTC),
        ),
        (
            date(2026, 10, 25),
            datetime(2026, 10, 25, 16, 59, tzinfo=UTC),
            datetime(2026, 10, 25, 17, 0, tzinfo=UTC),
        ),
    ],
)
def test_showroom_visit_window_on_dst_sunday_ends_at_19_local(
    app_config: AppConfig, dst_sunday: date, last_in_window: datetime, first_after_window: datetime
) -> None:
    leads = frame(app_config, visit(1, last_in_window), visit(2, first_after_window))

    visits = weekly_showroom_visits(leads, dst_sunday, app_config)

    assert visits.day_total(dst_sunday) == 1
    assert visits.total == 1


def test_totals_by_showroom_day_and_reason_come_from_metrics(app_config: AppConfig) -> None:
    old = at(date(2026, 8, 1), 11)
    leads = frame(
        app_config,
        lead(1, at(MONDAY, 11), showroom="Cluj", source_name="Site"),
        lead(2, at(MONDAY, 12), showroom="Cluj", source_name="Telefon"),
        lead(3, at(MONDAY, 13), showroom="Brașov", source_name="Site"),
        lost(4, old, "BUGET", status_changed_at=at(MONDAY, 11), showroom="Cluj"),
        lost(5, old, "BUGET", status_changed_at=at(MONDAY, 12), showroom="Cluj"),
        lost(6, old, "TIMP", status_changed_at=at(MONDAY, 13), showroom="Cluj"),
        lost(7, old, "NU_RASPUNS", status_changed_at=at(MONDAY, 14), showroom=None),
    )

    tables = weekly_lead_tables(leads, SUNDAY, app_config)
    losses = weekly_loss_reasons(leads, SUNDAY, app_config)

    assert tables.showroom_total("Cluj") == 2
    assert tables.day_showroom_total(MONDAY, "Cluj") == 2
    assert tables.day_source_totals(MONDAY) == {"Site": 2, "Telefon": 1, None: 0}
    assert losses.showroom_total("Cluj") == 3
    assert losses.reasons_by_count[0] == "BUGET"
    assert set(losses.reasons_by_count) == {"BUGET", "TIMP", "NU_RASPUNS"}
