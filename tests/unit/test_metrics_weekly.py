from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from digest.config import AppConfig
from digest.metrics.daily import (
    PreviousSnapshot,
    daily_window,
    daily_window_days,
    lead_row_flags,
)
from digest.metrics.frame import prepare_lead_frame
from digest.metrics.kpi import LeadCounts
from digest.metrics.weekly import (
    LEAD_ROW_COLUMNS,
    LossReasons,
    converted_count,
    converted_count_by_showroom,
    irrelevant_rows,
    relative_change,
    week_days,
    week_over_week,
    weekly_funnel,
    weekly_irrelevant,
    weekly_lead_rows,
    weekly_lead_tables,
    weekly_loss_reasons,
    weekly_showroom_revenire_count,
    weekly_showroom_visit_rows,
    weekly_showroom_visits,
    weekly_window,
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


PHONE_KEY, OTHER_PHONE_KEY = "a" * 64, "c" * 64


def visits_with_revenire_and_partner(app_config: AppConfig) -> pd.DataFrame:
    # Revenire 3: телефон встречался в понедельник, приход в среду. Revenire 5: телефон из
    # прошлой недели. Лид 4 делит телефон с утренним лидом того же окна, это визит.
    wednesday = MONDAY + timedelta(days=2)
    return frame(
        app_config,
        lead(1, at(MONDAY, 11), source_name="WhatsApp", contact_phone_key=PHONE_KEY),
        visit(2, at(MONDAY, 12), showroom="Cluj"),
        visit(3, at(wednesday, 12), showroom="Cluj", contact_phone_key=PHONE_KEY),
        lead(6, at(SUNDAY, 10), source_name="WhatsApp", contact_phone_key=OTHER_PHONE_KEY),
        visit(4, at(SUNDAY, 15), showroom="Brașov", contact_phone_key=OTHER_PHONE_KEY),
        lead(7, at(MONDAY - timedelta(days=3), 11), contact_phone_key="d" * 64),
        visit(5, at(SUNDAY, 16), showroom="Brașov", contact_phone_key="d" * 64),
        visit(8, at(wednesday, 13), showroom="Cluj", category="PARTNERSHIP"),
    )


def test_weekly_visits_equal_sum_of_daily_visits(app_config: AppConfig) -> None:
    leads = visits_with_revenire_and_partner(app_config)
    time_settings = app_config.status_mapping.time
    visits = weekly_showroom_visits(leads, SUNDAY, app_config)
    daily_revenire = 0

    for day in week_days(SUNDAY):
        d1_rows = lead_row_flags(leads, daily_window(day, time_settings), app_config)
        assert sum(visits.counts[day].values()) == int(d1_rows["showroom_visits"].sum()), day
        # Revenire в d1 это часть строки Alte.
        daily_revenire += int((d1_rows["leads_other"] & leads["is_showroom_revenire"]).sum())

    assert visits.total == 2
    assert weekly_showroom_revenire_count(leads, SUNDAY, app_config) == daily_revenire == 2


def test_weekly_visits_exclude_partner_and_revenire(app_config: AppConfig) -> None:
    visits = weekly_showroom_visits(
        visits_with_revenire_and_partner(app_config), SUNDAY, app_config
    )

    assert visits.counts[MONDAY]["Cluj"] == 1
    assert visits.counts[SUNDAY]["Brașov"] == 1
    assert visits.total == 2


def test_weekly_revenire_count(app_config: AppConfig) -> None:
    leads = visits_with_revenire_and_partner(app_config)

    assert weekly_showroom_revenire_count(leads, SUNDAY, app_config) == 2
    assert weekly_showroom_revenire_count(leads, SUNDAY - timedelta(days=7), app_config) == 0


def test_weekly_leads_still_exclude_every_showroom_source_lead(app_config: AppConfig) -> None:
    tables = weekly_lead_tables(visits_with_revenire_and_partner(app_config), SUNDAY, app_config)

    assert tables.total == 2


def test_week_over_week_visits_exclude_revenire_in_both_weeks(app_config: AppConfig) -> None:
    this_week = visits_with_revenire_and_partner(app_config)
    previous_week_rows = [
        lead(11, at(MONDAY - timedelta(days=14), 11), contact_phone_key="e" * 64),
        visit(12, at(MONDAY - timedelta(days=6), 12), contact_phone_key="e" * 64),
        visit(13, at(MONDAY - timedelta(days=5), 12)),
    ]
    leads = pd.concat([this_week, frame(app_config, *previous_week_rows)], ignore_index=True)

    change = week_over_week(leads, None, SUNDAY, app_config)

    assert (change.showroom_visits, change.showroom_visits_previous) == (2, 1)


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


def test_converted_count_by_showroom_sums_to_converted_count(app_config: AppConfig) -> None:
    lead_frame = frame(
        app_config,
        lead(1, at(date(2026, 8, 1), 11), converted_at=at(SUNDAY, 17)),
        lead(2, at(date(2026, 8, 1), 11), converted_at=at(MONDAY, 9), showroom="Cluj"),
        lead(3, at(date(2026, 8, 1), 11), converted_at=at(SUNDAY, 18), showroom=None),
        lead(4, at(date(2026, 8, 1), 11), converted_at=at(SUNDAY, 18), showroom="Iași"),
        lead(5, at(date(2026, 8, 1), 11), converted_at=at(SUNDAY, 19)),
    )
    window = weekly_window(SUNDAY, app_config.status_mapping.time)

    by_showroom = converted_count_by_showroom(lead_frame, window, app_config)

    expected: dict[str | None, int] = {
        **dict.fromkeys(app_config.status_mapping.showrooms, 0),
        "București": 1,
        "Cluj": 1,
        "Iași": 1,
        None: 1,
    }
    assert by_showroom == expected
    assert sum(by_showroom.values()) == converted_count(lead_frame, window) == 4


def irrelevant(lead_id: int, created_at: datetime, **overrides: Any) -> dict[str, Any]:
    return lost(lead_id, created_at, "IRELEVANT", **overrides)


def site_leads(first_id: int, count: int, day: date, **overrides: Any) -> list[dict[str, Any]]:
    return [lead(first_id + offset, at(day, 12), **overrides) for offset in range(count)]


def test_weekly_irrelevant_total_equals_weekly_funnel_irr(app_config: AppConfig) -> None:
    lead_frame = frame(
        app_config,
        *site_leads(1, 3, SUNDAY),
        irrelevant(10, at(SUNDAY, 12), source_name=None),
        irrelevant(11, at(MONDAY, 12), source_name="Showroom"),
    )

    result = weekly_irrelevant(lead_frame, None, SUNDAY, app_config)

    assert result.total_irr == weekly_funnel(lead_frame, SUNDAY, app_config).kpis.irr == 0.4


def test_weekly_irrelevant_ranks_by_irr_above_min_leads(app_config: AppConfig) -> None:
    lead_frame = frame(
        app_config,
        *site_leads(1, 8, SUNDAY),
        *[irrelevant(100 + offset, at(SUNDAY, 12)) for offset in range(2)],
        *site_leads(200, 7, SUNDAY, source_name="Telefon"),
        *[irrelevant(300 + offset, at(SUNDAY, 12), source_name="Telefon") for offset in range(3)],
        *[irrelevant(400 + offset, at(SUNDAY, 12), source_name="Mail") for offset in range(9)],
        *[irrelevant(500 + offset, at(SUNDAY, 12), source_name=None) for offset in range(12)],
    )

    result = weekly_irrelevant(lead_frame, None, SUNDAY, app_config)

    assert [(row.key, row.leads, row.irr_leads) for row in result.by_source] == [
        ("Telefon", 10, 3),
        ("Site", 10, 2),
    ]
    assert [row.meets_target for row in result.by_source] == [False, True]
    assert result.by_campaign == ()


def test_weekly_irrelevant_breaks_ties_by_name(app_config: AppConfig) -> None:
    lead_frame = frame(
        app_config,
        *site_leads(1, 8, SUNDAY, source_name="WhatsApp"),
        *[irrelevant(100 + offset, at(SUNDAY, 12), source_name="WhatsApp") for offset in range(2)],
        *site_leads(200, 8, SUNDAY, source_name="Telefon"),
        *[irrelevant(300 + offset, at(SUNDAY, 12), source_name="Telefon") for offset in range(2)],
    )

    result = weekly_irrelevant(lead_frame, None, SUNDAY, app_config)

    assert [row.key for row in result.by_source] == ["Telefon", "WhatsApp"]


def test_weekly_irrelevant_previous_week_uses_week_ago_snapshot(app_config: AppConfig) -> None:
    previous_sunday = SUNDAY - timedelta(days=7)
    last_week = site_leads(1, 10, previous_sunday)
    current = site_leads(100, 10, SUNDAY)
    # Лид прошлой недели стал IRELEVANT только после её воскресенья.
    today_rows = [*last_week[1:], irrelevant(1, at(previous_sunday, 12)), *current]

    result = weekly_irrelevant(
        frame(app_config, *today_rows),
        PreviousSnapshot(previous_sunday, frame(app_config, *last_week)),
        SUNDAY,
        app_config,
    )

    assert result.total_irr_previous == 0
    assert result.by_source[0].irr_previous == 0
    assert result.previous_snapshot_date == previous_sunday


def test_weekly_irrelevant_without_week_ago_has_no_previous(app_config: AppConfig) -> None:
    lead_frame = frame(app_config, *site_leads(1, 10, SUNDAY))

    result = weekly_irrelevant(lead_frame, None, SUNDAY, app_config)

    assert result.total_irr_previous is None
    assert result.by_source[0].irr_previous is None
    assert result.previous_snapshot_date is None


def test_irrelevant_rows_reject_threshold_that_admits_key_without_leads(
    app_config: AppConfig,
) -> None:
    no_leads = LeadCounts(**dict.fromkeys(LeadCounts.__dataclass_fields__, 0))

    with pytest.raises(ValueError, match="IRR не определён"):
        irrelevant_rows({"Site": no_leads}, None, 0, 5, app_config)


def test_reason_share_is_part_of_all_losses_and_of_showroom_losses() -> None:
    losses = LossReasons(
        reasons=("NU_RASPUNS", "BUGET"),
        by_showroom={
            "Cluj": {"NU_RASPUNS": 3, "BUGET": 1},
            None: {"NU_RASPUNS": 1, "BUGET": 0},
        },
    )

    assert losses.reason_share("NU_RASPUNS") == 4 / 5
    assert losses.reason_share("BUGET") == 1 / 5
    assert losses.showroom_reason_share("Cluj", "BUGET") == 1 / 4
    assert losses.showroom_reason_share(None, "NU_RASPUNS") == 1


def test_reason_share_without_losses_is_none() -> None:
    losses = LossReasons(reasons=("BUGET",), by_showroom={"Cluj": {"BUGET": 0}})

    assert losses.reason_share("BUGET") is None
    assert losses.showroom_reason_share("Cluj", "BUGET") is None
