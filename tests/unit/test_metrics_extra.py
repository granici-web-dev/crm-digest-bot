from datetime import date, datetime
from typing import Any

import pytest

from digest.config import AppConfig
from digest.metrics.extra import cohort_conversion, overdue_revenire, period_delta
from digest.metrics.frame import prepare_lead_frame
from digest.metrics.kpi import Period
from factories import BUCHAREST, make_snapshot_row

TODAY = date(2026, 9, 25)


def overdue_ids(rows: list[dict[str, Any]], config: AppConfig) -> list[int]:
    overdue = overdue_revenire(prepare_lead_frame(rows, config), TODAY, config)
    return sorted(overdue["lead_id"].tolist())


@pytest.mark.parametrize(
    ("data_revenire", "is_overdue"), [(date(2026, 9, 25), True), (date(2026, 9, 26), False)]
)
def test_revenire_is_overdue_from_its_date(
    app_config: AppConfig, data_revenire: date, is_overdue: bool
) -> None:
    rows = [make_snapshot_row(lead_id=1, data_revenire=data_revenire)]

    assert overdue_ids(rows, app_config) == ([1] if is_overdue else [])


@pytest.mark.parametrize(
    ("status_changed_at", "is_overdue"),
    [
        (None, True),
        (datetime(2026, 9, 19, 23, 0, tzinfo=BUCHAREST), True),
        # 00:30 по Бухаресту = 21:30 UTC накануне: день берётся по Бухаресту, не по UTC.
        (datetime(2026, 9, 20, 0, 30, tzinfo=BUCHAREST), False),
        (datetime(2026, 9, 20, 9, 0, tzinfo=BUCHAREST), False),
    ],
)
def test_status_change_on_or_after_revenire_date_clears_it(
    app_config: AppConfig, status_changed_at: datetime | None, is_overdue: bool
) -> None:
    rows = [
        make_snapshot_row(
            lead_id=1, data_revenire=date(2026, 9, 20), status_changed_at=status_changed_at
        )
    ]

    assert overdue_ids(rows, app_config) == ([1] if is_overdue else [])


def test_overdue_revenire_covers_open_stand_by_and_unmapped(app_config: AppConfig) -> None:
    due = {"data_revenire": date(2026, 9, 20)}
    rows = [
        make_snapshot_row(lead_id=1, category="ACTIVE", **due),
        make_snapshot_row(lead_id=2, category="ACTIVE_FOLLOWUP", **due),
        make_snapshot_row(lead_id=3, category="LOST", loss_reason="STAND_BY", **due),
        make_snapshot_row(lead_id=4, category="UNMAPPED", **due),
        make_snapshot_row(lead_id=5, category="WON", **due),
        make_snapshot_row(lead_id=6, category="PARTNERSHIP", **due),
        make_snapshot_row(lead_id=7, category="LOST", loss_reason="BUGET", **due),
    ]

    assert overdue_ids(rows, app_config) == [1, 2, 3, 4]


def test_cohort_conversion_counts_clients_among_useful_leads_of_the_month(
    app_config: AppConfig,
) -> None:
    in_may = datetime(2026, 5, 10, tzinfo=BUCHAREST)
    rows = [
        make_snapshot_row(lead_id=1, created_at=in_may, category="WON"),
        make_snapshot_row(lead_id=2, created_at=in_may),
        make_snapshot_row(lead_id=3, created_at=in_may, category="LOST", loss_reason="IRELEVANT"),
        make_snapshot_row(
            lead_id=4, created_at=datetime(2026, 6, 1, tzinfo=BUCHAREST), category="WON"
        ),
    ]
    may = Period(
        start=datetime(2026, 5, 1, tzinfo=BUCHAREST), end=datetime(2026, 6, 1, tzinfo=BUCHAREST)
    )

    assert cohort_conversion(prepare_lead_frame(rows, app_config), may, app_config) == 0.5


@pytest.mark.parametrize(
    ("current", "previous", "delta"),
    [
        (15, 10, 0.5),
        (5, 10, -0.5),
        (3, 0, None),
        (0.2, 0.1, 1.0),
        (None, 0.1, None),
        (0.1, None, None),
    ],
)
def test_delta_is_relative_and_none_from_zero_or_null(
    current: float | None, previous: float | None, delta: float | None
) -> None:
    assert period_delta(current, previous) == pytest.approx(delta)
