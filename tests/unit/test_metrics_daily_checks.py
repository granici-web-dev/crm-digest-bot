from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import pytest

from digest.config import AppConfig
from digest.metrics.daily import LEAD_ROWS, PreviousSnapshot, seller_format_counts
from digest.metrics.daily_checks import (
    IrelevantSpike,
    OverdueGroup,
    OverdueRevenire,
    UntouchedGroup,
    UntouchedLeads,
    anomalies,
    overdue_revenire_by_manager,
    same_weekday_comparison,
    stale_offers,
    untouched_leads,
)
from digest.metrics.frame import prepare_lead_frame
from factories import BUCHAREST, make_snapshot_row

REPORT_DATE = date(2026, 9, 25)
WINDOW_END = datetime(2026, 9, 25, 19, 0, tzinfo=BUCHAREST)


def frame(rows: list[dict[str, Any]], config: AppConfig) -> pd.DataFrame:
    return prepare_lead_frame(rows, config)


def new_lead(lead_id: int, created_at: datetime, **overrides: Any) -> dict[str, Any]:
    # Лид с сайта без касаний: last_contact_at = created_at («Contactat astăzi» по умолчанию).
    return make_snapshot_row(
        lead_id=lead_id, created_at=created_at, last_contact_at=created_at, **overrides
    )


def untouched(rows: list[dict[str, Any]], config: AppConfig) -> tuple[UntouchedGroup, ...]:
    return untouched_leads(frame(rows, config), REPORT_DATE, config).groups


# d2


def test_untouched_lead_is_reported_with_age_from_window_end(app_config: AppConfig) -> None:
    rows = [new_lead(1, datetime(2026, 9, 25, 10, 30, tzinfo=BUCHAREST))]

    assert untouched(rows, app_config) == (UntouchedGroup("Dragoi Mihaela", 1, 8),)


@pytest.mark.parametrize(
    "touch",
    [
        {"status_changed_at": datetime(2026, 9, 25, 11, 0, tzinfo=BUCHAREST)},
        {"last_contact_at": datetime(2026, 9, 25, 11, 0, tzinfo=BUCHAREST)},
        {"ofertat": True},
    ],
    ids=["status_change", "last_contact_after_creation", "ofertat"],
)
def test_touched_lead_is_not_reported(app_config: AppConfig, touch: dict[str, Any]) -> None:
    created_at = datetime(2026, 9, 25, 10, 0, tzinfo=BUCHAREST)
    rows = [
        make_snapshot_row(
            lead_id=1, created_at=created_at, **{"last_contact_at": created_at, **touch}
        )
    ]

    assert untouched(rows, app_config) == ()


def test_lead_created_by_consultant_is_touched(app_config: AppConfig) -> None:
    created_at = datetime(2026, 9, 25, 10, 0, tzinfo=BUCHAREST)
    rows = [
        new_lead(1, created_at, created_by_id=12, source_name="Telefon"),
        # Лид, заведённый маркетингом, консультант ещё не видел.
        new_lead(2, created_at, created_by_id=7),
    ]

    assert untouched(rows, app_config) == (UntouchedGroup("Dragoi Mihaela", 1, 9),)


@pytest.mark.parametrize(
    ("created_at", "expected"),
    [
        (datetime(2026, 9, 25, 15, 1, tzinfo=BUCHAREST), ()),
        (datetime(2026, 9, 25, 15, 0, tzinfo=BUCHAREST), ()),
        (
            datetime(2026, 9, 25, 14, 59, tzinfo=BUCHAREST),
            (UntouchedGroup("Dragoi Mihaela", 1, 4),),
        ),
    ],
    ids=["3h59", "exactly_4h", "4h01"],
)
def test_lead_younger_than_threshold_is_not_reported(
    app_config: AppConfig, created_at: datetime, expected: tuple[UntouchedGroup, ...]
) -> None:
    assert untouched([new_lead(1, created_at)], app_config) == expected


@pytest.mark.parametrize(
    ("created_at", "is_reported"),
    [
        (datetime(2026, 9, 22, 18, 59, tzinfo=BUCHAREST), False),
        (datetime(2026, 9, 22, 19, 0, tzinfo=BUCHAREST), True),
        (datetime(2026, 9, 25, 19, 0, tzinfo=BUCHAREST), False),
    ],
    ids=["before_lookback", "lookback_start", "window_end"],
)
def test_lead_older_than_lookback_is_not_reported(
    app_config: AppConfig, created_at: datetime, is_reported: bool
) -> None:
    assert bool(untouched([new_lead(1, created_at)], app_config)) is is_reported


def test_not_taken_lead_is_reported_even_if_touched(app_config: AppConfig) -> None:
    created_at = datetime(2026, 9, 25, 9, 0, tzinfo=BUCHAREST)
    rows = [
        new_lead(
            1,
            created_at,
            assigned_to_id=7,
            assigned_to_name="Marketing Sofa",
            status_changed_at=datetime(2026, 9, 25, 9, 30, tzinfo=BUCHAREST),
        )
    ]

    assert untouched(rows, app_config) == (UntouchedGroup(None, 1, 10),)


def test_lead_without_assignee_is_not_taken(app_config: AppConfig) -> None:
    created_at = datetime(2026, 9, 25, 9, 0, tzinfo=BUCHAREST)
    rows = [new_lead(1, created_at, assigned_to_id=None, assigned_to_name=None)]

    assert untouched(rows, app_config) == (UntouchedGroup(None, 1, 10),)


def test_showroom_visit_is_not_untouched(app_config: AppConfig) -> None:
    rows = [new_lead(1, datetime(2026, 9, 25, 9, 0, tzinfo=BUCHAREST), source_name="Showroom")]

    assert untouched(rows, app_config) == ()


def test_partnership_is_not_untouched(app_config: AppConfig) -> None:
    rows = [
        new_lead(
            1,
            datetime(2026, 9, 25, 9, 0, tzinfo=BUCHAREST),
            category="PARTNERSHIP",
            status_name="DESIGNER",
        )
    ]

    assert untouched(rows, app_config) == ()


def test_untouched_groups_put_not_taken_first_then_larger_groups(app_config: AppConfig) -> None:
    def at(hour: int) -> datetime:
        return datetime(2026, 9, 25, hour, 0, tzinfo=BUCHAREST)

    roibu = {"assigned_to_id": 8, "assigned_to_name": "Roibu Valeria"}
    rows = [
        new_lead(1, at(9)),
        new_lead(2, at(12), **roibu),
        new_lead(3, at(8), **roibu),
        new_lead(4, at(14), assigned_to_id=None, assigned_to_name=None),
    ]

    result = untouched_leads(frame(rows, app_config), REPORT_DATE, app_config)

    assert result == UntouchedLeads(
        lead_count=4,
        oldest_age_hours=11,
        groups=(
            UntouchedGroup(None, 1, 5),
            UntouchedGroup("Roibu Valeria", 2, 11),
            UntouchedGroup("Dragoi Mihaela", 1, 10),
        ),
    )


# d3


def test_revenire_today_is_not_overdue(app_config: AppConfig) -> None:
    rows = [make_snapshot_row(lead_id=1, data_revenire=REPORT_DATE)]

    result = overdue_revenire_by_manager(frame(rows, app_config), REPORT_DATE, app_config)

    assert result == OverdueRevenire(lead_count=0, max_days_overdue=None, groups=())


def test_overdue_days_are_counted_per_manager(app_config: AppConfig) -> None:
    godja = {"assigned_to_id": 10, "assigned_to_name": "Godja Adina Maria"}
    rows = [
        make_snapshot_row(lead_id=1, data_revenire=date(2026, 9, 19), **godja),
        make_snapshot_row(lead_id=2, data_revenire=date(2026, 9, 24), **godja),
        make_snapshot_row(lead_id=3, data_revenire=date(2026, 9, 23)),
        make_snapshot_row(
            lead_id=4,
            data_revenire=date(2026, 9, 24),
            assigned_to_id=7,
            assigned_to_name="Marketing Sofa",
        ),
    ]

    assert overdue_revenire_by_manager(
        frame(rows, app_config), REPORT_DATE, app_config
    ) == OverdueRevenire(
        lead_count=4,
        max_days_overdue=6,
        groups=(
            OverdueGroup(None, 1, 1),
            OverdueGroup("Godja Adina Maria", 2, 6),
            OverdueGroup("Dragoi Mihaela", 1, 2),
        ),
    )


# d4


def offer(lead_id: int, last_contact_day: int, **overrides: Any) -> dict[str, Any]:
    return make_snapshot_row(
        lead_id=lead_id,
        ofertat=True,
        created_at=datetime(2026, 8, 1, 12, 0, tzinfo=BUCHAREST),
        last_contact_at=datetime(2026, 9, last_contact_day, 12, 0, tzinfo=BUCHAREST),
        **overrides,
    )


def test_offer_on_day_14_is_not_stale(app_config: AppConfig) -> None:
    rows = [offer(1, 10), offer(2, 11)]

    result = stale_offers(frame(rows, app_config), None, REPORT_DATE, app_config)

    assert result.total == 1


def test_stale_offers_are_counted_by_showroom(app_config: AppConfig) -> None:
    rows = [
        offer(1, 1, showroom="Cluj"),
        offer(2, 2, showroom="Cluj"),
        offer(3, 3, showroom="Brașov"),
        offer(4, 4, showroom=None),
        # Контракт и IRELEVANT висящей офертой не считаются (ACTIVE_OFFERS_14).
        offer(5, 5, category="WON", status_name="Clienți"),
        offer(6, 6, category="LOST", loss_reason="IRELEVANT", status_name="IRELEVANT"),
    ]

    result = stale_offers(frame(rows, app_config), None, REPORT_DATE, app_config)

    assert result.by_showroom == {"Brașov": 1, "București": 0, "Cluj": 2, None: 1}
    assert result.total == 4
    assert result.change_since_yesterday is None


@pytest.mark.parametrize(
    ("previous_date", "expected_change"), [(date(2026, 9, 24), 1), (date(2026, 9, 23), None)]
)
def test_delta_needs_snapshot_of_exactly_yesterday(
    app_config: AppConfig, previous_date: date, expected_change: int | None
) -> None:
    today_rows = [offer(1, 1), offer(2, 10)]
    # Вчера оферта 2 ещё не висела (14 дней к 24.09), оферта 1 уже висела.
    previous = PreviousSnapshot(previous_date, frame([offer(1, 1), offer(2, 10)], app_config))

    result = stale_offers(frame(today_rows, app_config), previous, REPORT_DATE, app_config)

    assert result.total == 2
    assert result.change_since_yesterday == expected_change


# d5


def site_leads_per_day(per_day: int, days_back: range) -> list[dict[str, Any]]:
    return [
        new_lead(
            days * 100 + number,
            datetime.combine(REPORT_DATE - timedelta(days=days), datetime.min.time(), BUCHAREST)
            + timedelta(hours=12),
        )
        for days in days_back
        for number in range(per_day)
    ]


def test_site_zero_fires_when_average_reaches_threshold(app_config: AppConfig) -> None:
    rows = site_leads_per_day(2, range(1, 8))

    result = anomalies(frame(rows, app_config), REPORT_DATE, app_config)

    assert result.site_zero_previous_average == 2.0


def test_site_zero_silent_below_threshold(app_config: AppConfig) -> None:
    rows = site_leads_per_day(2, range(1, 8))[1:]

    assert (
        anomalies(frame(rows, app_config), REPORT_DATE, app_config).site_zero_previous_average
        is None
    )


def test_site_zero_silent_when_site_brought_a_lead(app_config: AppConfig) -> None:
    # 24.09 19:00 это начало сегодняшнего окна.
    rows = [
        *site_leads_per_day(2, range(1, 8)),
        new_lead(1, datetime(2026, 9, 24, 19, 0, tzinfo=BUCHAREST)),
    ]

    assert (
        anomalies(frame(rows, app_config), REPORT_DATE, app_config).site_zero_previous_average
        is None
    )


def test_other_web_sources_do_not_mask_site_zero(app_config: AppConfig) -> None:
    rows = [
        *site_leads_per_day(2, range(1, 8)),
        new_lead(
            1, datetime(2026, 9, 25, 12, 0, tzinfo=BUCHAREST), source_name="FacebookMessanger"
        ),
        new_lead(2, datetime(2026, 9, 25, 12, 0, tzinfo=BUCHAREST), source_name="Meta ADS"),
    ]

    assert (
        anomalies(frame(rows, app_config), REPORT_DATE, app_config).site_zero_previous_average
        == 2.0
    )


def irelevant(lead_id: int, **overrides: Any) -> dict[str, Any]:
    return make_snapshot_row(
        lead_id=lead_id,
        category="LOST",
        loss_reason="IRELEVANT",
        status_name="IRELEVANT",
        **{"created_at": datetime(2026, 9, 20, 12, 0, tzinfo=BUCHAREST), **overrides},
    )


@pytest.mark.parametrize(("lead_count", "is_spike"), [(4, False), (5, True)])
def test_irelevant_spike_counts_status_change_in_window(
    app_config: AppConfig, lead_count: int, is_spike: bool
) -> None:
    marked_at = datetime(2026, 9, 25, 12, 0, tzinfo=BUCHAREST)
    rows = [irelevant(lead_id, status_changed_at=marked_at) for lead_id in range(lead_count)]

    result = anomalies(frame(rows, app_config), REPORT_DATE, app_config)

    expected = (IrelevantSpike("Dragoi Mihaela", lead_count),) if is_spike else ()
    assert result.irelevant_spikes == expected


def test_irelevant_set_at_creation_counts_by_created_at(app_config: AppConfig) -> None:
    created_today = datetime(2026, 9, 25, 12, 0, tzinfo=BUCHAREST)
    rows = [
        *[irelevant(lead_id, created_at=created_today) for lead_id in range(5)],
        # Отмечен до окна: не всплеск сегодня, хотя лид создан в окне.
        irelevant(
            10,
            created_at=datetime(2026, 9, 24, 19, 30, tzinfo=BUCHAREST),
            status_changed_at=datetime(2026, 9, 24, 18, 0, tzinfo=BUCHAREST),
        ),
    ]

    result = anomalies(frame(rows, app_config), REPORT_DATE, app_config)

    assert result.irelevant_spikes == (IrelevantSpike("Dragoi Mihaela", 5),)


def test_irelevant_marked_at_window_end_belongs_to_next_day(app_config: AppConfig) -> None:
    rows = [irelevant(lead_id, status_changed_at=WINDOW_END) for lead_id in range(5)]

    assert anomalies(frame(rows, app_config), REPORT_DATE, app_config).irelevant_spikes == ()


# d6


def test_leads_match_d1_total(app_config: AppConfig) -> None:
    today = datetime(2026, 9, 25, 12, 0, tzinfo=BUCHAREST)
    rows = [
        new_lead(1, today),
        new_lead(2, today, source_name="Telefon"),
        new_lead(3, today, category="PARTNERSHIP", status_name="DESIGNER"),
        new_lead(4, today, source_name="Showroom"),
        new_lead(5, datetime(2026, 9, 18, 12, 0, tzinfo=BUCHAREST)),
    ]
    lead_frame = frame(rows, app_config)

    result = same_weekday_comparison(lead_frame, REPORT_DATE, app_config)

    d1_total = seller_format_counts(lead_frame, None, REPORT_DATE, app_config).total
    assert result.leads == sum(getattr(d1_total, row) for row in LEAD_ROWS) == 3
    assert result.leads_week_ago == 1
    assert result.week_ago_date == date(2026, 9, 18)


def test_contracts_counted_by_converted_at(app_config: AppConfig) -> None:
    def contract(lead_id: int, converted_at: datetime) -> dict[str, Any]:
        return make_snapshot_row(
            lead_id=lead_id,
            category="WON",
            status_name="Clienți",
            created_at=datetime(2026, 8, 1, 12, 0, tzinfo=BUCHAREST),
            converted_at=converted_at,
        )

    rows = [
        contract(1, datetime(2026, 9, 25, 10, 0, tzinfo=BUCHAREST)),
        contract(2, datetime(2026, 9, 24, 19, 0, tzinfo=BUCHAREST)),
        contract(3, datetime(2026, 9, 25, 19, 0, tzinfo=BUCHAREST)),
        contract(4, datetime(2026, 9, 18, 18, 59, tzinfo=BUCHAREST)),
    ]

    result = same_weekday_comparison(frame(rows, app_config), REPORT_DATE, app_config)

    assert (result.contracts, result.contracts_week_ago) == (2, 1)


def test_same_weekday_window_crosses_dst(app_config: AppConfig) -> None:
    # 25.10.2026 переход на зимнее время: окно 24.10 19:00 → 25.10 19:00 длится 25 часов.
    rows = [
        new_lead(1, datetime(2026, 10, 24, 19, 0, tzinfo=BUCHAREST)),
        new_lead(2, datetime(2026, 10, 25, 18, 59, tzinfo=BUCHAREST)),
        new_lead(3, datetime(2026, 10, 17, 19, 0, tzinfo=BUCHAREST)),
        new_lead(4, datetime(2026, 10, 18, 19, 0, tzinfo=BUCHAREST)),
    ]

    result = same_weekday_comparison(frame(rows, app_config), date(2026, 10, 25), app_config)

    assert (result.leads, result.leads_week_ago) == (2, 1)
