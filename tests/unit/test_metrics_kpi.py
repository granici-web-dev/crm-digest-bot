from dataclasses import asdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest

from digest.config import AppConfig, Manager, ManagerRoster
from digest.metrics.frame import prepare_lead_frame
from digest.metrics.kpi import (
    LeadCounts,
    Period,
    kpis_from,
    lead_counts,
    lead_counts_by_manager,
    lead_counts_by_showroom,
)
from factories import BUCHAREST, etalon_lead_rows, load_etalon, make_snapshot_row

ETALON_PERIOD = Period(
    start=datetime(2026, 5, 1, tzinfo=BUCHAREST), end=datetime(2026, 6, 17, tzinfo=BUCHAREST)
)
ETALON_ANALYSIS_DATE = date(2026, 6, 27)


def test_kpi_matches_etalon(app_config: AppConfig) -> None:
    etalon = load_etalon()
    roster = ManagerRoster(
        managers=[
            Manager(id=manager_id, name=name, showroom=None, active=True)
            for name, manager_id in etalon["managers"].items()
        ]
    )
    config = app_config.model_copy(update={"managers": roster})
    lead_frame = prepare_lead_frame(etalon_lead_rows(etalon, config.status_mapping), config)

    assert set(etalon["expected_by_agent"]) == set(etalon["managers"])
    counts_by_manager = lead_counts_by_manager(
        lead_frame, ETALON_PERIOD, ETALON_ANALYSIS_DATE, config
    )
    for name, manager_id in etalon["managers"].items():
        assert_matches_expected(
            counts_by_manager[manager_id], etalon["expected_by_agent"][name], name
        )

    company = lead_counts(lead_frame, ETALON_PERIOD, ETALON_ANALYSIS_DATE, config)
    assert_matches_expected(company, etalon["expected_company"], "company")

    counts_by_showroom = lead_counts_by_showroom(
        lead_frame, ETALON_PERIOD, ETALON_ANALYSIS_DATE, config
    )
    expected_by_showroom = {entry["showroom"]: entry for entry in etalon["expected_by_showroom"]}
    assert set(counts_by_showroom) == set(expected_by_showroom)
    for showroom, expected in expected_by_showroom.items():
        assert_matches_expected(counts_by_showroom[showroom], expected, str(showroom))


def assert_matches_expected(counts: LeadCounts, expected: dict[str, Any], label: str) -> None:
    assert asdict(counts) == expected["counts"], label
    assert asdict(kpis_from(counts)) == pytest.approx(expected["kpi"], abs=1e-9), label


SEPTEMBER = Period(
    start=datetime(2026, 9, 1, tzinfo=BUCHAREST), end=datetime(2026, 10, 1, tzinfo=BUCHAREST)
)
SEPTEMBER_END = date(2026, 9, 30)
IN_SEPTEMBER = datetime(2026, 9, 10, 12, 0, tzinfo=BUCHAREST)


def company_counts(rows: list[dict[str, Any]], config: AppConfig, **overrides: Any) -> LeadCounts:
    arguments: dict[str, Any] = {"period": SEPTEMBER, "analysis_date": SEPTEMBER_END}
    arguments.update(overrides)
    return lead_counts(prepare_lead_frame(rows, config), config=config, **arguments)


def test_partnership_is_not_a_lead(app_config: AppConfig) -> None:
    rows = [
        make_snapshot_row(lead_id=1, created_at=IN_SEPTEMBER),
        make_snapshot_row(lead_id=2, created_at=IN_SEPTEMBER, category="PARTNERSHIP"),
    ]

    assert company_counts(rows, app_config).leads == 1


def test_unmapped_lead_is_a_useful_lead(app_config: AppConfig) -> None:
    rows = [make_snapshot_row(created_at=IN_SEPTEMBER, category="UNMAPPED", status_name=None)]

    counts = company_counts(rows, app_config)

    assert (counts.leads, counts.useful) == (1, 1)


def test_unassigned_lead_counts_in_company_total(app_config: AppConfig) -> None:
    rows = [make_snapshot_row(created_at=IN_SEPTEMBER, assigned_to_id=None)]

    assert company_counts(rows, app_config).leads == 1


def test_period_includes_start_and_excludes_end(app_config: AppConfig) -> None:
    rows = [
        make_snapshot_row(lead_id=1, created_at=SEPTEMBER.start),
        make_snapshot_row(lead_id=2, created_at=SEPTEMBER.end),
        make_snapshot_row(lead_id=3, created_at=SEPTEMBER.start - timedelta(seconds=1)),
    ]

    assert company_counts(rows, app_config).leads == 1


def test_daily_window_boundary_is_19_bucharest(app_config: AppConfig) -> None:
    daily_window = Period(
        start=datetime(2026, 9, 22, 19, 0, tzinfo=BUCHAREST),
        end=datetime(2026, 9, 23, 19, 0, tzinfo=BUCHAREST),
    )
    rows = [
        make_snapshot_row(lead_id=1, created_at=datetime(2026, 9, 23, 15, 59, tzinfo=UTC)),
        make_snapshot_row(lead_id=2, created_at=datetime(2026, 9, 23, 16, 0, tzinfo=UTC)),
    ]

    assert company_counts(rows, app_config, period=daily_window).leads == 1


def test_showroom_conversion_counts_only_clients_among_visits(app_config: AppConfig) -> None:
    client = {"created_at": IN_SEPTEMBER, "category": "WON", "status_name": "Clienți"}
    rows = [
        make_snapshot_row(lead_id=1, source_name="Showroom", **client),
        make_snapshot_row(lead_id=2, source_name="Site", **client),
        make_snapshot_row(lead_id=3, source_name="Showroom", created_at=IN_SEPTEMBER),
    ]

    assert kpis_from(company_counts(rows, app_config)).sc == 0.5


@pytest.mark.parametrize(
    ("last_contact_at", "analysis_date", "is_stale"),
    [
        (datetime(2026, 9, 16, 23, 30, tzinfo=BUCHAREST), SEPTEMBER_END, False),
        (datetime(2026, 9, 15, 23, 30, tzinfo=BUCHAREST), SEPTEMBER_END, True),
        # 00:30 по Бухаресту = 21:30 UTC накануне: день контакта по Бухаресту, не по UTC.
        (datetime(2026, 9, 16, 0, 30, tzinfo=BUCHAREST), SEPTEMBER_END, False),
        # 14 дней через переход на зимнее время 25.10.2026.
        (datetime(2026, 10, 18, 0, 30, tzinfo=BUCHAREST), date(2026, 11, 1), False),
        (datetime(2026, 10, 17, 23, 30, tzinfo=BUCHAREST), date(2026, 11, 1), True),
    ],
    ids=["14_days", "15_days", "after_midnight", "dst_autumn_14", "dst_autumn_15"],
)
def test_offer_is_stale_after_more_than_14_days_without_contact(
    app_config: AppConfig, last_contact_at: datetime, analysis_date: date, is_stale: bool
) -> None:
    row = make_snapshot_row(created_at=IN_SEPTEMBER, ofertat=True, last_contact_at=last_contact_at)
    period = Period(start=SEPTEMBER.start, end=datetime(2026, 11, 1, tzinfo=BUCHAREST))

    counts = company_counts([row], app_config, period=period, analysis_date=analysis_date)

    assert counts.active_offers_14 == int(is_stale)


def test_offer_without_last_contact_is_not_stale(app_config: AppConfig) -> None:
    row = make_snapshot_row(created_at=IN_SEPTEMBER, ofertat=True, last_contact_at=None)

    assert company_counts([row], app_config).active_offers_14 == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"category": "WON", "status_name": "Clienți"},
        {"category": "LOST", "loss_reason": "IRELEVANT", "status_name": "IRELEVANT"},
    ],
)
def test_won_or_irrelevant_offer_is_not_stale(
    app_config: AppConfig, overrides: dict[str, Any]
) -> None:
    row = make_snapshot_row(
        created_at=IN_SEPTEMBER,
        ofertat=True,
        last_contact_at=datetime(2026, 8, 1, tzinfo=BUCHAREST),
        **overrides,
    )

    assert company_counts([row], app_config).active_offers_14 == 0


def test_every_kpi_is_none_without_leads(app_config: AppConfig) -> None:
    kpis = kpis_from(company_counts([], app_config))

    assert set(asdict(kpis).values()) == {None}


def test_price_lost_rate_excludes_irrelevant_and_no_answer(app_config: AppConfig) -> None:
    lost = {"created_at": IN_SEPTEMBER, "category": "LOST"}
    rows = [
        make_snapshot_row(lead_id=1, loss_reason="BUGET", **lost),
        make_snapshot_row(lead_id=2, loss_reason="IRELEVANT", **lost),
        make_snapshot_row(lead_id=3, loss_reason="NU_RASPUNS", **lost),
        make_snapshot_row(lead_id=4, created_at=IN_SEPTEMBER),
    ]

    assert kpis_from(company_counts(rows, app_config)).plr == 0.5


def test_manager_slice_has_zero_row_for_active_manager_without_leads(
    app_config: AppConfig,
) -> None:
    counts = lead_counts_by_manager(
        prepare_lead_frame([], app_config), SEPTEMBER, SEPTEMBER_END, app_config
    )

    assert counts[12].leads == 0


def test_manager_slice_skips_inactive_manager(app_config: AppConfig) -> None:
    marketing_account_id = 7
    rows = [make_snapshot_row(created_at=IN_SEPTEMBER, assigned_to_id=marketing_account_id)]

    counts = lead_counts_by_manager(
        prepare_lead_frame(rows, app_config), SEPTEMBER, SEPTEMBER_END, app_config
    )

    assert marketing_account_id not in counts


def test_showroom_slice_keeps_leads_without_showroom_under_none(app_config: AppConfig) -> None:
    rows = [
        make_snapshot_row(lead_id=1, created_at=IN_SEPTEMBER, showroom="Cluj"),
        make_snapshot_row(lead_id=2, created_at=IN_SEPTEMBER, showroom=None),
    ]

    counts = lead_counts_by_showroom(
        prepare_lead_frame(rows, app_config), SEPTEMBER, SEPTEMBER_END, app_config
    )

    assert (counts["Cluj"].leads, counts[None].leads, counts["București"].leads) == (1, 1, 0)


def test_unmapped_lead_is_counted_separately(app_config: AppConfig) -> None:
    rows = [
        make_snapshot_row(lead_id=1, created_at=IN_SEPTEMBER, category="UNMAPPED", status_name="X"),
        make_snapshot_row(lead_id=2, created_at=IN_SEPTEMBER),
    ]

    counts = company_counts(rows, app_config)

    assert (counts.leads, counts.useful, counts.unmapped) == (2, 2, 1)


def test_showroom_outside_config_list_gets_its_own_row(app_config: AppConfig) -> None:
    rows = [
        make_snapshot_row(lead_id=1, created_at=IN_SEPTEMBER, showroom="Bucuresti"),
        make_snapshot_row(lead_id=2, created_at=IN_SEPTEMBER, showroom="Cluj"),
    ]

    counts = lead_counts_by_showroom(
        prepare_lead_frame(rows, app_config), SEPTEMBER, SEPTEMBER_END, app_config
    )

    assert list(counts) == ["București", "Brașov", "Cluj", "Bucuresti", None]
    assert (counts["Bucuresti"].leads, counts["Cluj"].leads) == (1, 1)


@pytest.mark.parametrize(
    ("window_day", "included_utc", "excluded_utc"),
    [
        # 29.03.2026: переход на летнее время, начало окна 19:00 EET, конец 19:00 EEST.
        (
            date(2026, 3, 29),
            [datetime(2026, 3, 28, 17, 0, tzinfo=UTC), datetime(2026, 3, 29, 15, 59, tzinfo=UTC)],
            [datetime(2026, 3, 28, 16, 59, tzinfo=UTC), datetime(2026, 3, 29, 16, 0, tzinfo=UTC)],
        ),
        # 25.10.2026: переход на зимнее время, начало окна 19:00 EEST, конец 19:00 EET.
        (
            date(2026, 10, 25),
            [datetime(2026, 10, 24, 16, 0, tzinfo=UTC), datetime(2026, 10, 25, 16, 59, tzinfo=UTC)],
            [datetime(2026, 10, 24, 15, 59, tzinfo=UTC), datetime(2026, 10, 25, 17, 0, tzinfo=UTC)],
        ),
    ],
    ids=["spring", "autumn"],
)
def test_daily_window_follows_bucharest_clock_across_dst(
    app_config: AppConfig,
    window_day: date,
    included_utc: list[datetime],
    excluded_utc: list[datetime],
) -> None:
    window = Period(
        start=datetime.combine(window_day - timedelta(days=1), time(19, 0), tzinfo=BUCHAREST),
        end=datetime.combine(window_day, time(19, 0), tzinfo=BUCHAREST),
    )
    rows = [
        make_snapshot_row(lead_id=lead_id, created_at=created_at)
        for lead_id, created_at in enumerate([*included_utc, *excluded_utc])
    ]

    counts = company_counts(rows, app_config, period=window, analysis_date=window_day)

    assert counts.leads == len(included_utc)
