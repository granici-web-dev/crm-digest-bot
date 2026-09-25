from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from digest.config import AppConfig
from digest.metrics.daily import SellerFormatRow, seller_format_counts
from digest.metrics.frame import prepare_lead_frame
from digest.metrics.kpi import Period
from factories import BUCHAREST, make_snapshot_row

PERIOD = Period(
    datetime(2026, 9, 24, 19, tzinfo=BUCHAREST), datetime(2026, 9, 25, 19, tzinfo=BUCHAREST)
)
IN_WINDOW = datetime(2026, 9, 25, 11, 0, tzinfo=BUCHAREST)
BEFORE_WINDOW = datetime(2026, 9, 20, 11, 0, tzinfo=BUCHAREST)


def frame(app_config: AppConfig, *rows: dict[str, Any]) -> pd.DataFrame:
    return prepare_lead_frame(list(rows), app_config)


def lead(lead_id: int, **overrides: Any) -> dict[str, Any]:
    return make_snapshot_row(**{"lead_id": lead_id, "created_at": IN_WINDOW, **overrides})


def old_lead(lead_id: int, **overrides: Any) -> dict[str, Any]:
    return make_snapshot_row(**{"lead_id": lead_id, "created_at": BEFORE_WINDOW, **overrides})


def test_window_includes_19_00_yesterday_and_excludes_19_00_today(app_config: AppConfig) -> None:
    today = frame(
        app_config,
        lead(1, created_at=datetime(2026, 9, 24, 18, 59, 59, tzinfo=BUCHAREST)),
        lead(2, created_at=datetime(2026, 9, 24, 19, 0, 0, tzinfo=BUCHAREST)),
        lead(3, created_at=datetime(2026, 9, 25, 18, 59, 59, tzinfo=BUCHAREST)),
        lead(4, created_at=datetime(2026, 9, 25, 19, 0, 0, tzinfo=BUCHAREST)),
    )

    counts = seller_format_counts(today, None, PERIOD, app_config)

    assert counts.total.leads_web == 2


def test_window_on_dst_end_day_reads_utc_timestamps_in_bucharest(app_config: AppConfig) -> None:
    period = Period(
        datetime(2026, 10, 24, 19, tzinfo=BUCHAREST), datetime(2026, 10, 25, 19, tzinfo=BUCHAREST)
    )
    utc = ZoneInfo("UTC")
    today = frame(
        app_config,
        lead(1, created_at=datetime(2026, 10, 24, 16, 0, tzinfo=utc)),
        lead(2, created_at=datetime(2026, 10, 25, 16, 59, tzinfo=utc)),
        lead(3, created_at=datetime(2026, 10, 25, 17, 0, tzinfo=utc)),
    )

    counts = seller_format_counts(today, None, period, app_config)

    assert counts.total.leads_web == 2


def test_lead_rows_are_mutually_exclusive_and_sum_to_leads(app_config: AppConfig) -> None:
    today = frame(
        app_config,
        lead(1, source_name="Site"),
        lead(2, source_name="Mail"),
        lead(3, source_name="Meta ADS"),
        lead(4, source_name="Telefon"),
        lead(5, source_name="WhatsApp"),
        lead(6, source_name="WhatsApp", category="PARTNERSHIP", status_name="DESIGNER"),
        lead(7, source_name="Colaborare"),
        lead(8, source_name="Showroom", category="ACTIVE_FOLLOWUP", status_name="Revenire 1"),
        lead(9, source_name="Recomandare"),
        lead(10, source_name="Showroom", status_name="IN PROCES"),
        lead(11, source_name="Sursa noua"),
    )

    total = seller_format_counts(today, None, PERIOD, app_config).total

    assert (
        total.leads_web,
        total.leads_phone,
        total.leads_whatsapp,
        total.leads_partner,
        total.leads_other,
    ) == (3, 1, 1, 2, 3)
    assert total.leads == 10


def test_visits_count_new_showroom_leads_and_moves_into_showroom_status(
    app_config: AppConfig,
) -> None:
    previous = frame(
        app_config,
        old_lead(1, status_name="IN PROCES"),
        old_lead(2, status_name="SHOWROOM"),
    )
    today = frame(
        app_config,
        old_lead(1, status_name="SHOWROOM"),
        old_lead(2, status_name="SHOWROOM"),
        lead(3, source_name="Showroom", status_name="SHOWROOM"),
        lead(4, source_name="Site", status_name="IN PROCES"),
        lead(5, source_name="Showroom", status_name="IN PROCES"),
    )

    counts = seller_format_counts(today, previous, PERIOD, app_config)

    assert counts.total.showroom_visits == 3
    assert counts.total.leads == 1


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

    assert seller_format_counts(today, previous, PERIOD, app_config).total.offers == 3


def test_contract_counts_only_transition_to_won(app_config: AppConfig) -> None:
    won = {"category": "WON", "status_name": "Clienți"}
    previous = frame(app_config, old_lead(1), old_lead(2, **won))
    today = frame(app_config, old_lead(1, **won), old_lead(2, **won), lead(3, **won))

    assert seller_format_counts(today, previous, PERIOD, app_config).total.contracts == 2


def test_without_previous_snapshot_transition_rows_are_unknown(app_config: AppConfig) -> None:
    today = frame(app_config, lead(1, ofertat=True, source_name="Telefon"))

    counts = seller_format_counts(today, None, PERIOD, app_config)

    assert counts.has_previous_snapshot is False
    assert counts.total == SellerFormatRow(0, 1, 0, 0, 0, None, None, None)


def test_blocks_follow_config_order_and_todays_showroom(app_config: AppConfig) -> None:
    previous = frame(app_config, old_lead(1, showroom="Cluj", ofertat=False))
    today = frame(
        app_config,
        old_lead(1, showroom="Brașov", ofertat=True),
        lead(2, showroom="Iași"),
    )

    counts = seller_format_counts(today, previous, PERIOD, app_config)

    assert list(counts.by_showroom) == ["București", "Brașov", "Cluj", "Iași"]
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

    counts = seller_format_counts(today, previous, PERIOD, app_config)

    assert counts.without_showroom_lead_count == 2
    assert counts.total.leads == 2
    assert counts.total.offers == 1
    assert sum(row.leads for row in counts.by_showroom.values()) == 1
