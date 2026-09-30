from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import pytest

from digest.config import AppConfig
from digest.metrics.daily import LEAD_ROWS, PreviousSnapshot, seller_format_counts
from digest.metrics.daily_checks import (
    Anomalies,
    IrelevantSpike,
    MissingFollowupDate,
    MissingFollowupGroup,
    OverdueGroup,
    OverdueRevenire,
    RollingContractRate,
    SameWeekdayComparison,
    StaleOffers,
    UntouchedGroup,
    UntouchedLeads,
    anomalies,
    missing_followup_date,
    overdue_revenire_by_manager,
    rolling_contract_rate,
    same_weekday_comparison,
    stale_offers,
    trend_direction,
    untouched_leads,
)
from digest.metrics.frame import prepare_lead_frame
from digest.metrics.kpi import Period, lead_counts_by_showroom
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

    assert untouched(rows, app_config) == (UntouchedGroup("Dragoi Mihaela", 1, 8, (1,)),)


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

    assert untouched(rows, app_config) == (UntouchedGroup("Dragoi Mihaela", 1, 9, (2,)),)


@pytest.mark.parametrize(
    ("last_contact_at", "is_reported"),
    [
        (datetime(2026, 9, 25, 10, 1, tzinfo=BUCHAREST), True),
        (datetime(2026, 9, 25, 10, 1, 1, tzinfo=BUCHAREST), False),
        (None, True),
        (datetime(2026, 9, 25, 9, 55, tzinfo=BUCHAREST), True),
    ],
    ids=["60s_after_creation", "61s_after_creation", "empty", "before_creation"],
)
def test_contact_is_a_touch_only_beyond_tolerance(
    app_config: AppConfig, last_contact_at: datetime | None, is_reported: bool
) -> None:
    created_at = datetime(2026, 9, 25, 10, 0, tzinfo=BUCHAREST)
    rows = [make_snapshot_row(lead_id=1, created_at=created_at, last_contact_at=last_contact_at)]

    assert bool(untouched(rows, app_config)) is is_reported


def test_lead_created_by_inactive_user_is_not_touched(app_config: AppConfig) -> None:
    # id 6 в managers.yaml, но не консультант (active: false): его лид никто не обработал.
    rows = [new_lead(1, datetime(2026, 9, 25, 10, 0, tzinfo=BUCHAREST), created_by_id=6)]

    assert untouched(rows, app_config) == (UntouchedGroup("Dragoi Mihaela", 1, 9, (1,)),)


def test_untouched_age_across_dst_end_counts_real_hours(app_config: AppConfig) -> None:
    # 25.10.2026 переход на зимнее время: от 24.10 19:00 до 25.10 19:00 прошло 25 часов.
    rows = [new_lead(1, datetime(2026, 10, 24, 19, 0, tzinfo=BUCHAREST))]

    result = untouched_leads(frame(rows, app_config), date(2026, 10, 25), app_config)

    assert result.oldest_age_hours == 25


@pytest.mark.parametrize(
    ("created_at", "is_reported"),
    [
        (datetime(2026, 10, 22, 19, 0, tzinfo=BUCHAREST), True),
        (datetime(2026, 10, 22, 18, 59, tzinfo=BUCHAREST), False),
    ],
    ids=["lookback_start", "before_lookback"],
)
def test_lookback_across_dst_end_is_three_calendar_days(
    app_config: AppConfig, created_at: datetime, is_reported: bool
) -> None:
    result = untouched_leads(
        frame([new_lead(1, created_at)], app_config), date(2026, 10, 25), app_config
    )

    assert bool(result.groups) is is_reported


@pytest.mark.parametrize(
    ("created_at", "expected"),
    [
        (datetime(2026, 9, 25, 15, 1, tzinfo=BUCHAREST), ()),
        (datetime(2026, 9, 25, 15, 0, tzinfo=BUCHAREST), ()),
        (
            datetime(2026, 9, 25, 14, 59, tzinfo=BUCHAREST),
            (UntouchedGroup("Dragoi Mihaela", 1, 4, (1,)),),
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

    assert untouched(rows, app_config) == (UntouchedGroup(None, 1, 10, (1,)),)


def test_lead_without_assignee_is_not_taken(app_config: AppConfig) -> None:
    created_at = datetime(2026, 9, 25, 9, 0, tzinfo=BUCHAREST)
    rows = [new_lead(1, created_at, assigned_to_id=None, assigned_to_name=None)]

    assert untouched(rows, app_config) == (UntouchedGroup(None, 1, 10, (1,)),)


def test_showroom_visit_is_not_untouched(app_config: AppConfig) -> None:
    rows = [new_lead(1, datetime(2026, 9, 25, 9, 0, tzinfo=BUCHAREST), source_name="Showroom")]

    assert untouched(rows, app_config) == ()


def test_showroom_revenire_is_not_untouched(app_config: AppConfig) -> None:
    phone_key = "a" * 64
    rows = [
        make_snapshot_row(
            lead_id=1,
            created_at=datetime(2026, 8, 1, tzinfo=BUCHAREST),
            status_changed_at=datetime(2026, 8, 2, tzinfo=BUCHAREST),
            contact_phone_key=phone_key,
        ),
        new_lead(
            2,
            datetime(2026, 9, 25, 9, 0, tzinfo=BUCHAREST),
            source_name="Showroom",
            contact_phone_key=phone_key,
        ),
    ]
    lead_frame = prepare_lead_frame(rows, app_config).set_index("lead_id")
    assert lead_frame.loc[2, "is_showroom_revenire"]

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


def test_untouched_groups_put_not_taken_first_and_list_oldest_leads_first(
    app_config: AppConfig,
) -> None:
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
            UntouchedGroup(None, 1, 5, (4,)),
            UntouchedGroup("Roibu Valeria", 2, 11, (3, 2)),
            UntouchedGroup("Dragoi Mihaela", 1, 10, (1,)),
        ),
        lead_ids=(3, 1, 2, 4),
    )


# d3


def test_revenire_today_is_not_overdue(app_config: AppConfig) -> None:
    rows = [make_snapshot_row(lead_id=1, data_revenire=REPORT_DATE)]

    result = overdue_revenire_by_manager(frame(rows, app_config), REPORT_DATE, app_config)

    assert result == OverdueRevenire(lead_count=0, max_days_overdue=None, groups=(), lead_ids=())


def test_overdue_days_are_counted_per_manager_most_overdue_lead_first(
    app_config: AppConfig,
) -> None:
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
            OverdueGroup(None, 1, 1, (4,)),
            OverdueGroup("Godja Adina Maria", 2, 6, (1, 2)),
            OverdueGroup("Dragoi Mihaela", 1, 2, (3,)),
        ),
        lead_ids=(1, 3, 2, 4),
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
    assert result.lead_ids_by_showroom == {
        "Brașov": (3,),
        "București": (),
        "Cluj": (1, 2),
        None: (4,),
    }
    assert result.lead_ids == (1, 2, 3, 4)


def test_stale_offer_lead_ids_go_oldest_contact_first(app_config: AppConfig) -> None:
    rows = [
        offer(7, 9, showroom="Cluj"),
        offer(3, 2, showroom="Cluj"),
        offer(5, 2, showroom="Cluj"),
    ]

    result = stale_offers(frame(rows, app_config), None, REPORT_DATE, app_config)

    assert result.lead_ids_by_showroom["Cluj"] == (3, 5, 7)


def test_stale_offers_match_active_offers_14(app_config: AppConfig) -> None:
    rows = [
        offer(1, 1, showroom="Cluj"),
        offer(2, 2, category="ACTIVE_FOLLOWUP", status_name="Revenire 1", showroom="Brașov"),
        offer(3, 3, category="LOST", loss_reason="BUGET", status_name="BUGET", showroom="Cluj"),
        offer(4, 4, category="LOST", loss_reason="STAND_BY", status_name="Stand BY"),
    ]
    lead_frame = frame(rows, app_config)

    result = stale_offers(lead_frame, None, REPORT_DATE, app_config)

    all_time = Period(datetime(2026, 1, 1, tzinfo=BUCHAREST), WINDOW_END)
    counts = lead_counts_by_showroom(lead_frame, all_time, REPORT_DATE, app_config)
    assert result.by_showroom == {
        showroom: showroom_counts.active_offers_14 for showroom, showroom_counts in counts.items()
    }
    assert result.by_showroom == {"Brașov": 1, "București": 0, "Cluj": 1, None: 0}


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


def test_irelevant_spikes_put_not_taken_first(app_config: AppConfig) -> None:
    marked_at = datetime(2026, 9, 25, 12, 0, tzinfo=BUCHAREST)
    not_taken = {"assigned_to_id": None, "assigned_to_name": None}
    rows = [
        *[irelevant(lead_id, status_changed_at=marked_at) for lead_id in range(6)],
        *[
            irelevant(lead_id, status_changed_at=marked_at, **not_taken)
            for lead_id in range(10, 15)
        ],
    ]

    result = anomalies(frame(rows, app_config), REPORT_DATE, app_config)

    assert result.irelevant_spikes == (
        IrelevantSpike(None, 5),
        IrelevantSpike("Dragoi Mihaela", 6),
    )


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

    d1_total = seller_format_counts(lead_frame, None, None, REPORT_DATE, app_config).total
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


# d7


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=BUCHAREST)


def contract(lead_id: int, converted_at: datetime, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "category": "WON",
        "status_name": "Clienți",
        "created_at": datetime(2026, 5, 1, 12, 0, tzinfo=BUCHAREST),
    }
    return make_snapshot_row(lead_id=lead_id, converted_at=converted_at, **(fields | overrides))


def test_rolling_contract_rate_counts_contracts_by_converted_at_and_useful_by_created_at(
    app_config: AppConfig,
) -> None:
    this_window = datetime(2026, 9, 10, 12, 0, tzinfo=BUCHAREST)
    previous_window = datetime(2026, 8, 10, 12, 0, tzinfo=BUCHAREST)
    rows = [
        # Договор окна от лида, созданного до обоих окон: в знаменатель не входит.
        contract(1, this_window),
        # Лид окна, подписан в окне: и в числителе, и в знаменателе.
        contract(2, this_window, created_at=this_window),
        new_lead(3, this_window),
        new_lead(4, this_window),
        new_lead(5, this_window, category="LOST", loss_reason="IRELEVANT", status_name="IRELEVANT"),
        contract(6, previous_window),
        new_lead(7, previous_window),
        new_lead(8, previous_window),
    ]

    result = rolling_contract_rate(frame(rows, app_config), REPORT_DATE, app_config)

    assert (result.contracts, result.useful, result.rate) == (2, 3, 2 / 3)
    assert (result.previous_contracts, result.previous_useful, result.previous_rate) == (1, 2, 0.5)
    assert (result.difference_pp, result.direction) == (16.666666667, "up")


def test_rolling_window_starts_at_1900_of_day_before_first_day(app_config: AppConfig) -> None:
    # Отчёт 25.09: текущие 30 окон [26.08 19:00, 25.09 19:00), прошлые [27.07 19:00, 26.08 19:00).
    first_day_start = datetime(2026, 8, 26, 19, 0, tzinfo=BUCHAREST)
    previous_start = datetime(2026, 7, 27, 19, 0, tzinfo=BUCHAREST)
    rows = [
        new_lead(1, first_day_start - timedelta(minutes=1)),
        new_lead(2, first_day_start),
        new_lead(3, WINDOW_END - timedelta(minutes=1)),
        new_lead(4, WINDOW_END),
        new_lead(5, previous_start),
        new_lead(6, previous_start - timedelta(minutes=1)),
    ]

    result = rolling_contract_rate(frame(rows, app_config), REPORT_DATE, app_config)

    assert (result.useful, result.previous_useful) == (2, 2)


def test_rolling_numerator_equals_sum_of_d6_contracts(app_config: AppConfig) -> None:
    rows = [
        contract(lead_id, at(REPORT_DATE - timedelta(days=days_back), hour))
        for lead_id, (days_back, hour) in enumerate(
            [(0, 10), (0, 19), (3, 18), (15, 19), (29, 18), (29, 19), (30, 18), (30, 19), (45, 9)]
        )
    ]
    lead_frame = frame(rows, app_config)

    result = rolling_contract_rate(lead_frame, REPORT_DATE, app_config)

    d6_contracts = sum(
        same_weekday_comparison(
            lead_frame, REPORT_DATE - timedelta(days=days_back), app_config
        ).contracts
        for days_back in range(30)
    )
    assert result.contracts == d6_contracts == 6


def test_partnership_contract_counts_useful_does_not(app_config: AppConfig) -> None:
    this_window = datetime(2026, 9, 10, 12, 0, tzinfo=BUCHAREST)
    rows = [
        contract(
            1, this_window, created_at=this_window, category="PARTNERSHIP", status_name="DESIGNER"
        ),
        new_lead(2, this_window),
    ]

    result = rolling_contract_rate(frame(rows, app_config), REPORT_DATE, app_config)

    assert (result.contracts, result.useful) == (1, 1)


def test_rolling_window_across_dst_has_thirty_daily_windows(app_config: AppConfig) -> None:
    # 25.10.2026 переход на зимнее время: окно дня 25.10 длится 25 часов, начало 30 окон
    # отчёта 10.11 это 11.10 19:00 по летнему времени.
    rows = [
        new_lead(1, datetime(2026, 10, 11, 18, 59, tzinfo=BUCHAREST)),
        new_lead(2, datetime(2026, 10, 11, 19, 0, tzinfo=BUCHAREST)),
        new_lead(3, datetime(2026, 10, 25, 18, 30, tzinfo=BUCHAREST)),
        new_lead(4, datetime(2026, 11, 10, 18, 59, tzinfo=BUCHAREST)),
    ]

    result = rolling_contract_rate(frame(rows, app_config), date(2026, 11, 10), app_config)

    assert (result.useful, result.previous_useful) == (3, 1)


@pytest.mark.parametrize(
    ("difference_pp", "direction"),
    [(1.99, "flat"), (-1.99, "flat"), (2.0, "up"), (-2.0, "down"), (None, None)],
)
def test_direction_flat_below_threshold_and_arrow_at_threshold(
    difference_pp: float | None, direction: str | None
) -> None:
    assert trend_direction(difference_pp, 2.0) == direction


@pytest.mark.parametrize(("previous_useful", "direction"), [(50, "up"), (49, "flat")])
def test_rate_difference_at_threshold_is_not_lost_to_float_error(
    app_config: AppConfig, previous_useful: int, direction: str
) -> None:
    # 3/50 − 2/50 это ровно 2,0 п.п.; 3/50 − 2/49 это 1,92 п.п.
    this_window = datetime(2026, 9, 10, 12, 0, tzinfo=BUCHAREST)
    previous_window = datetime(2026, 8, 10, 12, 0, tzinfo=BUCHAREST)
    current = [
        contract(lead_id, this_window, created_at=this_window)
        if lead_id < 3
        else new_lead(lead_id, this_window)
        for lead_id in range(50)
    ]
    previous = [
        contract(100 + lead_id, previous_window, created_at=previous_window)
        if lead_id < 2
        else new_lead(100 + lead_id, previous_window)
        for lead_id in range(previous_useful)
    ]

    result = rolling_contract_rate(frame(current + previous, app_config), REPORT_DATE, app_config)

    assert result.direction == direction


def test_zero_useful_gives_no_rate_and_no_direction(app_config: AppConfig) -> None:
    rows = [
        contract(1, datetime(2026, 9, 10, 12, 0, tzinfo=BUCHAREST)),
        new_lead(2, datetime(2026, 8, 10, 12, 0, tzinfo=BUCHAREST)),
    ]

    result = rolling_contract_rate(frame(rows, app_config), REPORT_DATE, app_config)

    assert (result.contracts, result.useful, result.rate) == (1, 0, None)
    assert (result.previous_rate, result.difference_pp, result.direction) == (0.0, None, None)


def test_previous_zero_useful_gives_no_direction(app_config: AppConfig) -> None:
    rows = [new_lead(1, datetime(2026, 9, 10, 12, 0, tzinfo=BUCHAREST))]

    result = rolling_contract_rate(frame(rows, app_config), REPORT_DATE, app_config)

    assert (result.rate, result.previous_rate, result.direction) == (0.0, None, None)


# d3: без Data revenire

REVENIRE_1 = {"category": "ACTIVE_FOLLOWUP", "status_name": "Revenire 1"}
STAND_BY = {"category": "LOST", "loss_reason": "STAND_BY", "status_name": "Stand BY"}


def followup_lead(lead_id: int, status_age: timedelta, **overrides: Any) -> dict[str, Any]:
    return make_snapshot_row(
        lead_id=lead_id,
        status_changed_at=WINDOW_END - status_age,
        **{"created_at": datetime(2026, 9, 1, 12, 0, tzinfo=BUCHAREST), **REVENIRE_1, **overrides},
    )


def missing(rows: list[dict[str, Any]], config: AppConfig) -> MissingFollowupDate:
    return missing_followup_date(frame(rows, config), REPORT_DATE, config)


@pytest.mark.parametrize(
    ("status_age", "reported"),
    [
        (timedelta(hours=24), False),
        (timedelta(hours=24, seconds=1), True),
    ],
    ids=["exactly_24h", "24h_and_1s"],
)
def test_followup_without_data_revenire_is_reported_only_after_24_hours(
    app_config: AppConfig, status_age: timedelta, reported: bool
) -> None:
    result = missing([followup_lead(1, status_age)], app_config)

    assert result.lead_ids == ((1,) if reported else ())


@pytest.mark.parametrize(
    ("created_age", "reported"),
    [(timedelta(hours=23), False), (timedelta(hours=25), True)],
    ids=["created_23h_ago", "created_25h_ago"],
)
def test_null_status_changed_at_counts_age_from_created_at(
    app_config: AppConfig, created_age: timedelta, reported: bool
) -> None:
    row = make_snapshot_row(
        lead_id=1, created_at=WINDOW_END - created_age, status_changed_at=None, **REVENIRE_1
    )

    assert missing([row], app_config).lead_ids == ((1,) if reported else ())


def test_only_stand_by_and_revenire_statuses_are_checked(app_config: AppConfig) -> None:
    week = timedelta(days=7)
    rows = [
        followup_lead(1, week),
        followup_lead(2, week, **STAND_BY),
        followup_lead(3, week, category="ACTIVE", status_name="IN PROCES"),
        followup_lead(
            4, week, category="LOST", loss_reason="NU_RASPUNS", status_name="NU A RASPUNS"
        ),
        followup_lead(5, week, category="PARTNERSHIP", status_name="DESIGNER"),
        followup_lead(6, week, category="WON", status_name="Clienți"),
        followup_lead(7, week, category="UNMAPPED", status_name=None),
    ]

    assert missing(rows, app_config).lead_ids == (1, 2)


def test_lead_with_data_revenire_is_not_reported(app_config: AppConfig) -> None:
    rows = [followup_lead(1, timedelta(days=7), data_revenire=date(2026, 10, 1))]

    assert missing(rows, app_config) == MissingFollowupDate(0, (), (), 0, False)


def test_lead_created_before_leads_created_from_is_not_checked(app_config: AppConfig) -> None:
    rows = [
        followup_lead(
            1, timedelta(days=7), created_at=datetime(2026, 5, 31, 23, 59, tzinfo=BUCHAREST)
        ),
        followup_lead(
            2, timedelta(days=7), created_at=datetime(2026, 6, 1, 0, 0, tzinfo=BUCHAREST)
        ),
    ]

    assert missing(rows, app_config).lead_ids == (2,)


def test_test_account_lead_is_not_checked(app_config: AppConfig) -> None:
    rows = [followup_lead(1, timedelta(days=7), assigned_to_id=4, assigned_to_name="Potinga Dima")]

    assert missing(rows, app_config) == MissingFollowupDate(0, (), (), 0, False)


def test_groups_are_ordered_like_overdue_not_taken_first(app_config: AppConfig) -> None:
    raileanu = {"assigned_to_id": 13, "assigned_to_name": "Raileanu  Leon"}
    roibu = {"assigned_to_id": 8, "assigned_to_name": "Roibu Valeria"}
    rows = [
        followup_lead(1, timedelta(days=2), **raileanu),
        followup_lead(2, timedelta(days=3), **raileanu),
        followup_lead(3, timedelta(days=5), **roibu),
        followup_lead(4, timedelta(days=9), assigned_to_id=7, assigned_to_name="Marketing Sofa"),
        followup_lead(5, timedelta(days=4), assigned_to_id=None, assigned_to_name=None),
        followup_lead(6, timedelta(days=6), assigned_to_id=2, assigned_to_name="Palega Andrei"),
    ]

    assert missing(rows, app_config) == MissingFollowupDate(
        lead_count=6,
        groups=(
            MissingFollowupGroup(None, 2, (4, 5)),
            MissingFollowupGroup("Raileanu  Leon", 2, (2, 1)),
            MissingFollowupGroup("Palega Andrei", 1, (6,)),
            MissingFollowupGroup("Roibu Valeria", 1, (3,)),
        ),
        lead_ids=(4, 6, 3, 5, 2, 1),
        unreadable_count=0,
        field_unavailable=False,
    )


def test_groups_follow_assigned_to_id_with_name_from_lead(app_config: AppConfig) -> None:
    week = timedelta(days=7)
    rows = [
        followup_lead(1, week, assigned_to_id=99, assigned_to_name="Nou Consultant"),
        followup_lead(2, week, assigned_to_id=98, assigned_to_name="Nou Consultant"),
        followup_lead(3, week, assigned_to_id=99, assigned_to_name="Nou Consultant"),
    ]

    assert missing(rows, app_config).groups == (
        MissingFollowupGroup("Nou Consultant", 2, (1, 3)),
        MissingFollowupGroup("Nou Consultant", 1, (2,)),
    )


@pytest.mark.parametrize("problem", ["missing", "name_mismatch", "unexpected_value"])
def test_lead_with_unreadable_data_revenire_is_not_reported_as_empty(
    app_config: AppConfig, problem: str
) -> None:
    week = timedelta(days=7)
    rows = [followup_lead(1, week, data_revenire_problem=problem), followup_lead(2, week)]

    assert missing(rows, app_config) == MissingFollowupDate(
        lead_count=1,
        groups=(MissingFollowupGroup("Dragoi Mihaela", 1, (2,)),),
        lead_ids=(2,),
        unreadable_count=1,
        field_unavailable=False,
    )


def test_field_is_unavailable_when_unreadable_for_every_lead_in_scope(
    app_config: AppConfig,
) -> None:
    week = timedelta(days=7)
    rows = [
        followup_lead(1, week, data_revenire_problem="missing"),
        followup_lead(2, week, data_revenire_problem="name_mismatch"),
        # Вне охвата: его читаемое поле не делает блок доступным.
        followup_lead(3, week, category="ACTIVE", status_name="IN PROCES"),
    ]

    assert missing(rows, app_config) == MissingFollowupDate(0, (), (), 2, True)


# Пустой кадр


def test_every_check_is_empty_on_empty_frame(app_config: AppConfig) -> None:
    lead_frame = frame([], app_config)
    yesterday = PreviousSnapshot(date(2026, 9, 24), lead_frame)

    assert untouched_leads(lead_frame, REPORT_DATE, app_config) == UntouchedLeads(0, None, (), ())
    assert overdue_revenire_by_manager(lead_frame, REPORT_DATE, app_config) == OverdueRevenire(
        0, None, (), ()
    )
    assert missing_followup_date(lead_frame, REPORT_DATE, app_config) == MissingFollowupDate(
        0, (), (), 0, False
    )
    no_offers = {"Brașov": 0, "București": 0, "Cluj": 0, None: 0}
    assert stale_offers(lead_frame, yesterday, REPORT_DATE, app_config) == StaleOffers(
        no_offers, 0, 0, dict.fromkeys(no_offers, ()), ()
    )
    assert anomalies(lead_frame, REPORT_DATE, app_config) == Anomalies(7, None, ())
    assert same_weekday_comparison(lead_frame, REPORT_DATE, app_config) == SameWeekdayComparison(
        date(2026, 9, 18), 0, 0, 0, 0
    )
    assert rolling_contract_rate(lead_frame, REPORT_DATE, app_config) == RollingContractRate(
        30, 0, 0, None, 0, 0, None, None, None
    )


def test_overdue_of_one_manager_keeps_only_that_group(app_config: AppConfig) -> None:
    godja = {"assigned_to_id": 10, "assigned_to_name": "Godja Adina Maria"}
    rows = [
        make_snapshot_row(lead_id=1, data_revenire=date(2026, 9, 19), **godja),
        make_snapshot_row(lead_id=2, data_revenire=date(2026, 9, 24), **godja),
        make_snapshot_row(lead_id=3, data_revenire=date(2026, 9, 23)),
    ]
    overdue = overdue_revenire_by_manager(frame(rows, app_config), REPORT_DATE, app_config)

    assert overdue.of_manager("Godja Adina Maria") == OverdueRevenire(
        2, 6, (OverdueGroup("Godja Adina Maria", 2, 6, (1, 2)),), (1, 2)
    )
    assert overdue.of_manager("Moaca Andreea") == OverdueRevenire(0, None, (), ())
