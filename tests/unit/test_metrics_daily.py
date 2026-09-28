from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from digest.config import AppConfig
from digest.metrics.daily import (
    PreviousSnapshot,
    SellerFormatCounts,
    SellerFormatRow,
    comparable_previous,
    daily_window,
    seller_format_counts,
)
from digest.metrics.frame import prepare_client_frame, prepare_lead_frame
from digest.metrics.kpi import Period
from factories import BUCHAREST, make_snapshot_row

REPORT_DATE = date(2026, 9, 25)
IN_WINDOW = datetime(2026, 9, 25, 11, 0, tzinfo=BUCHAREST)
BEFORE_WINDOW = datetime(2026, 9, 20, 11, 0, tzinfo=BUCHAREST)


def frame(app_config: AppConfig, *rows: dict[str, Any]) -> pd.DataFrame:
    return prepare_lead_frame(list(rows), app_config)


def yesterday(previous_frame: pd.DataFrame) -> PreviousSnapshot:
    return PreviousSnapshot(REPORT_DATE - timedelta(days=1), previous_frame)


def lead(lead_id: int, **overrides: Any) -> dict[str, Any]:
    return make_snapshot_row(**{"lead_id": lead_id, "created_at": IN_WINDOW, **overrides})


def old_lead(lead_id: int, **overrides: Any) -> dict[str, Any]:
    return make_snapshot_row(**{"lead_id": lead_id, "created_at": BEFORE_WINDOW, **overrides})


def counts_of(
    app_config: AppConfig,
    today: pd.DataFrame,
    previous: PreviousSnapshot | None = None,
    clients: pd.DataFrame | None = None,
) -> SellerFormatCounts:
    return seller_format_counts(today, previous, clients, REPORT_DATE, app_config)


def clients_frame(
    app_config: AppConfig, *clients: tuple[int, datetime, str | None]
) -> pd.DataFrame:
    return prepare_client_frame(
        [
            {"client_id": client_id, "created_at": created_at, "showroom": showroom}
            for client_id, created_at, showroom in clients
        ],
        app_config,
    )


PHONE_KEY = "a" * 64
EMAIL_KEY = "b" * 64
SHOWROOM = {"source_name": "Showroom", "status_name": "SHOWROOM"}
PARTNERSHIP = {"category": "PARTNERSHIP", "status_name": "DESIGNER"}


def test_window_includes_19_00_yesterday_and_excludes_19_00_today(app_config: AppConfig) -> None:
    today = frame(
        app_config,
        lead(1, created_at=datetime(2026, 9, 24, 18, 59, 59, tzinfo=BUCHAREST)),
        lead(2, created_at=datetime(2026, 9, 24, 19, 0, 0, tzinfo=BUCHAREST)),
        lead(3, created_at=datetime(2026, 9, 25, 18, 59, 59, tzinfo=BUCHAREST)),
        lead(4, created_at=datetime(2026, 9, 25, 19, 0, 0, tzinfo=BUCHAREST)),
    )

    assert counts_of(app_config, today).total.leads_web == 2


def test_window_on_dst_end_day_reads_utc_timestamps_in_bucharest(app_config: AppConfig) -> None:
    utc = ZoneInfo("UTC")
    today = frame(
        app_config,
        lead(1, created_at=datetime(2026, 10, 24, 16, 0, tzinfo=utc)),
        lead(2, created_at=datetime(2026, 10, 25, 16, 59, tzinfo=utc)),
        lead(3, created_at=datetime(2026, 10, 25, 17, 0, tzinfo=utc)),
    )

    counts = seller_format_counts(today, None, None, date(2026, 10, 25), app_config)

    assert counts.total.leads_web == 2


def test_lead_rows_are_mutually_exclusive_and_sum_to_leads(app_config: AppConfig) -> None:
    today = frame(
        app_config,
        old_lead(100, contact_phone_key=PHONE_KEY),
        lead(1, source_name="Site"),
        lead(2, source_name="Mail"),
        lead(3, source_name="Meta ADS"),
        lead(4, source_name="Telefon"),
        lead(5, source_name="WhatsApp"),
        lead(6, source_name="WhatsApp", **PARTNERSHIP),
        lead(7, source_name="Colaborare"),
        lead(8, source_name="Arhirtect"),
        lead(9, source_name="Recomandare"),
        lead(10, contact_phone_key=PHONE_KEY, **SHOWROOM),
        lead(11, source_name="Sursa noua"),
        lead(12, **SHOWROOM),
        lead(13, source_name="Showroom", **PARTNERSHIP),
    )

    total = counts_of(app_config, today).total

    assert (
        total.leads_web,
        total.leads_phone,
        total.leads_whatsapp,
        total.leads_partner,
        total.leads_other,
        total.showroom_visits,
    ) == (3, 1, 1, 4, 3, 1)
    assert total.leads == 12


# Leads Designer/Colaboratori


def test_new_partnership_lead_counts_as_partner(app_config: AppConfig) -> None:
    today = frame(app_config, lead(1, source_name="Telefon", **PARTNERSHIP), lead(2))

    total = counts_of(app_config, today).total

    assert (total.leads_partner, total.leads_phone, total.leads_web) == (1, 0, 1)


def test_arhirtect_source_counts_as_partner(app_config: AppConfig) -> None:
    today = frame(app_config, lead(1, source_name="Arhirtect"), lead(2, source_name="Colaborare"))

    total = counts_of(app_config, today).total

    assert (total.leads_partner, total.leads_other) == (2, 0)


def test_lead_moved_to_partnership_since_yesterday_counts_as_partner(
    app_config: AppConfig,
) -> None:
    previous = frame(app_config, old_lead(1), old_lead(2, **PARTNERSHIP), old_lead(3))
    today = frame(
        app_config,
        old_lead(1, **PARTNERSHIP),
        old_lead(2, **PARTNERSHIP),
        old_lead(3),
        old_lead(4, **PARTNERSHIP),
    )

    counts = counts_of(app_config, today, yesterday(previous))

    assert counts.total.leads_partner == 1
    assert counts.total.leads == 1
    assert counts.by_showroom["București"].leads_partner == 1
    assert counts.missing_from_previous_lead_ids == (4,)


def test_new_lead_moved_to_partnership_is_counted_once(app_config: AppConfig) -> None:
    # Снапшот вчера в 19:00:05 уже видел лид, созданный в 19:00:02: он и новый, и в разнице.
    just_after_start = datetime(2026, 9, 24, 19, 0, 2, tzinfo=BUCHAREST)
    previous = frame(app_config, lead(1, created_at=just_after_start))
    today = frame(app_config, lead(1, created_at=just_after_start, **PARTNERSHIP))

    assert counts_of(app_config, today, yesterday(previous)).total.leads_partner == 1


def test_partner_row_counts_only_new_leads_without_yesterday_snapshot(
    app_config: AppConfig,
) -> None:
    today = frame(app_config, old_lead(1, **PARTNERSHIP), lead(2, **PARTNERSHIP))

    counts = counts_of(app_config, today)

    assert counts.has_previous_snapshot is False
    assert (counts.total.leads_partner, counts.total.leads) == (1, 1)


# Vizita in showroom и Leads Alte/Showroom (revenire)


def test_showroom_lead_without_older_contact_is_visit(app_config: AppConfig) -> None:
    today = frame(
        app_config,
        old_lead(1, contact_phone_key="c" * 64, contact_email_key="d" * 64),
        lead(2, contact_phone_key=PHONE_KEY, contact_email_key=EMAIL_KEY, **SHOWROOM),
    )

    total = counts_of(app_config, today).total

    assert (total.showroom_visits, total.leads_other, total.leads) == (1, 0, 0)


def test_showroom_lead_matching_older_lead_by_phone_is_revenire(app_config: AppConfig) -> None:
    today = frame(
        app_config,
        old_lead(1, source_name="WhatsApp", contact_phone_key=PHONE_KEY, contact_email_key=None),
        lead(2, contact_phone_key=PHONE_KEY, contact_email_key=EMAIL_KEY, **SHOWROOM),
    )

    total = counts_of(app_config, today).total

    assert (total.showroom_visits, total.leads_other) == (0, 1)


def test_showroom_lead_matching_older_lead_by_email_is_revenire(app_config: AppConfig) -> None:
    today = frame(
        app_config,
        old_lead(1, category="LOST", status_name="IRELEVANT", contact_email_key=EMAIL_KEY),
        lead(2, contact_phone_key=PHONE_KEY, contact_email_key=EMAIL_KEY, **SHOWROOM),
    )

    total = counts_of(app_config, today).total

    assert (total.showroom_visits, total.leads_other) == (0, 1)


def test_repeat_visit_is_not_counted_as_visit(app_config: AppConfig) -> None:
    today = frame(
        app_config,
        old_lead(1, contact_phone_key=PHONE_KEY, **SHOWROOM),
        lead(2, contact_phone_key=PHONE_KEY, **SHOWROOM),
    )

    counts = counts_of(app_config, today)

    assert (counts.total.showroom_visits, counts.total.leads_other) == (0, 1)
    assert counts.by_showroom["București"].leads_other == 1


def test_contact_seen_earlier_in_the_same_window_is_still_a_visit(app_config: AppConfig) -> None:
    # Одна граница для обоих правил (ответ пользователя 28.09.2026): утром WhatsApp, вечером
    # шоурум это визит, а не revenire.
    morning = datetime(2026, 9, 25, 10, 0, tzinfo=BUCHAREST)
    today = frame(
        app_config,
        lead(1, source_name="WhatsApp", created_at=morning, contact_phone_key=PHONE_KEY),
        lead(2, contact_phone_key=PHONE_KEY, **SHOWROOM),
    )

    total = counts_of(app_config, today).total

    assert (total.showroom_visits, total.leads_whatsapp, total.leads_other) == (1, 1, 0)


def test_empty_keys_never_match(app_config: AppConfig) -> None:
    today = frame(
        app_config,
        old_lead(1, contact_phone_key=None, contact_email_key=None),
        lead(2, contact_phone_key=None, contact_email_key=None, **SHOWROOM),
    )

    assert counts_of(app_config, today).total.showroom_visits == 1


def test_status_showroom_no_longer_counts_as_visit(app_config: AppConfig) -> None:
    previous = frame(app_config, old_lead(1, status_name="IN PROCES"))
    today = frame(
        app_config,
        old_lead(1, status_name="SHOWROOM"),
        lead(2, source_name="Site", status_name="SHOWROOM"),
    )

    total = counts_of(app_config, today, yesterday(previous)).total

    assert (total.showroom_visits, total.leads_web) == (0, 1)


def test_visits_do_not_need_yesterday_snapshot(app_config: AppConfig) -> None:
    today = frame(app_config, lead(1, **SHOWROOM))

    assert counts_of(app_config, today).total.showroom_visits == 1


# Oferta


def test_offer_counts_only_transition_to_ofertat(app_config: AppConfig) -> None:
    previous = frame(
        app_config,
        old_lead(1, ofertat=False),
        old_lead(2, ofertat=True),
        old_lead(3, ofertat=None),
        old_lead(4, ofertat=None),
    )
    today = frame(
        app_config,
        old_lead(1, ofertat=True),
        old_lead(2, ofertat=True),
        old_lead(3, ofertat=True),
        old_lead(4, ofertat=None),
        lead(5, ofertat=True),
    )

    assert counts_of(app_config, today, yesterday(previous)).total.offers == 3


def test_without_previous_snapshot_offers_are_unknown(app_config: AppConfig) -> None:
    today = frame(app_config, lead(1, ofertat=True, source_name="Telefon"))

    counts = counts_of(app_config, today)

    assert counts.has_previous_snapshot is False
    assert counts.total == SellerFormatRow(0, 1, 0, 0, 0, 0, None, None)


# Contract Cantitate


def test_contracts_are_new_clients_in_window_by_showroom(app_config: AppConfig) -> None:
    today = frame(app_config, lead(1))
    clients = clients_frame(
        app_config,
        (1, datetime(2026, 9, 24, 19, 0, tzinfo=BUCHAREST), "Cluj"),
        (2, datetime(2026, 9, 25, 12, 0, tzinfo=BUCHAREST), "Cluj"),
        (3, datetime(2026, 9, 25, 12, 0, tzinfo=BUCHAREST), "Brașov"),
        (4, datetime(2026, 9, 24, 18, 59, tzinfo=BUCHAREST), "Cluj"),
        (5, datetime(2026, 9, 25, 19, 0, tzinfo=BUCHAREST), "Cluj"),
        (6, datetime(2026, 9, 25, 12, 0, tzinfo=BUCHAREST), None),
        (7, datetime(2026, 9, 25, 12, 0, tzinfo=BUCHAREST), "Iași"),
    )

    counts = counts_of(app_config, today, clients=clients)

    assert counts.has_clients_snapshot is True
    assert counts.total.contracts == 5
    assert {name: row.contracts for name, row in counts.by_showroom.items()} == {
        "Brașov": 1,
        "București": 0,
        "Cluj": 2,
        "Iași": 1,
    }
    assert counts.without_showroom_count == 1


def test_client_created_before_contracts_count_from_is_never_counted(
    app_config: AppConfig,
) -> None:
    today = frame(
        app_config,
        lead(1, created_at=datetime(2026, 5, 31, 12, 0, tzinfo=BUCHAREST)),
    )
    clients = clients_frame(
        app_config,
        (1, datetime(2026, 5, 31, 23, 59, tzinfo=BUCHAREST), "Cluj"),
        (2, datetime(2026, 6, 1, 0, 0, tzinfo=BUCHAREST), "Cluj"),
    )

    counts = seller_format_counts(today, None, clients, date(2026, 6, 1), app_config)

    assert counts.total.contracts == 1


def test_contracts_are_dash_without_clients_snapshot(app_config: AppConfig) -> None:
    today = frame(app_config, lead(1, category="WON", status_name="Clienți"))

    counts = counts_of(app_config, today)

    assert counts.has_clients_snapshot is False
    assert counts.total.contracts is None
    assert all(row.contracts is None for row in counts.by_showroom.values())


def test_won_status_of_lead_is_not_a_contract(app_config: AppConfig) -> None:
    won = {"category": "WON", "status_name": "Clienți"}
    previous = frame(app_config, old_lead(1))
    today = frame(app_config, old_lead(1, **won), lead(2, **won))

    counts = counts_of(app_config, today, yesterday(previous), clients_frame(app_config))

    assert counts.total.contracts == 0


# Блоки, окна, снапшоты


def test_blocks_follow_config_order_and_todays_showroom(app_config: AppConfig) -> None:
    previous = frame(app_config, old_lead(1, showroom="Cluj", ofertat=False))
    today = frame(
        app_config,
        old_lead(1, showroom="Brașov", ofertat=True),
        lead(2, showroom="Iași"),
    )

    counts = counts_of(app_config, today, yesterday(previous))

    assert list(counts.by_showroom) == ["Brașov", "București", "Cluj", "Iași"]
    assert counts.by_showroom["Brașov"].offers == 1
    assert counts.by_showroom["Cluj"].offers == 0
    assert counts.by_showroom["Iași"].leads == 1


def test_lead_without_showroom_is_in_total_and_counted_separately(app_config: AppConfig) -> None:
    previous = frame(app_config, old_lead(1, showroom=None))
    today = frame(
        app_config,
        old_lead(1, showroom=None, ofertat=True),
        lead(2, showroom=None),
        old_lead(3, showroom=None),
        lead(4, showroom="Cluj"),
    )

    counts = counts_of(app_config, today, yesterday(previous))

    assert counts.without_showroom_count == 2
    assert counts.total.leads == 2
    assert counts.total.offers == 1
    assert sum(row.leads for row in counts.by_showroom.values()) == 1


def test_daily_window_is_19_00_yesterday_to_19_00_report_date(app_config: AppConfig) -> None:
    assert daily_window(REPORT_DATE, app_config.status_mapping.time) == Period(
        datetime(2026, 9, 24, 19, tzinfo=BUCHAREST), datetime(2026, 9, 25, 19, tzinfo=BUCHAREST)
    )


def test_previous_snapshot_older_than_yesterday_is_not_diffed(app_config: AppConfig) -> None:
    previous = frame(app_config, old_lead(1, ofertat=False))
    today = frame(app_config, old_lead(1, ofertat=True))
    two_days_ago = PreviousSnapshot(REPORT_DATE - timedelta(days=2), previous)

    counts = counts_of(app_config, today, two_days_ago)

    assert counts.has_previous_snapshot is False
    assert counts.total.offers is None


def test_old_lead_missing_from_previous_is_no_transition_and_is_reported(
    app_config: AppConfig,
) -> None:
    previous = frame(app_config, old_lead(1))
    today = frame(
        app_config,
        old_lead(1),
        old_lead(2, ofertat=True, **PARTNERSHIP),
        lead(4, ofertat=True),
    )

    counts = counts_of(app_config, today, yesterday(previous))

    assert (counts.total.offers, counts.total.leads_partner) == (1, 0)
    assert counts.missing_from_previous_lead_ids == (2,)


def test_other_sources_go_to_alte_and_unknown_ones_are_reported(app_config: AppConfig) -> None:
    today = frame(
        app_config,
        lead(1, source_name="Recomandare"),
        lead(2, source_name="Client Fidel"),
        lead(3, source_name="Sursa noua"),
        lead(4, source_name=None),
        lead(5, source_name="Telefon"),
        old_lead(6, source_name="Sursa noua"),
    )

    counts = counts_of(app_config, today)

    assert counts.total.leads_other == 4
    assert counts.total.leads_phone == 1
    assert counts.unknown_source_lead_ids == (3, 4)


@pytest.mark.parametrize(
    ("snapshot_date", "is_comparable"),
    [(date(2026, 9, 24), True), (date(2026, 9, 23), False), (date(2026, 9, 25), False)],
    ids=["yesterday", "two_days_ago", "same_day"],
)
def test_only_snapshot_of_exactly_yesterday_is_comparable(
    snapshot_date: date, is_comparable: bool
) -> None:
    previous = PreviousSnapshot(snapshot_date, pd.DataFrame())

    assert (comparable_previous(previous, REPORT_DATE) is previous) is is_comparable
    assert comparable_previous(None, REPORT_DATE) is None
