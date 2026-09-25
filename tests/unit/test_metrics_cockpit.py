from collections.abc import Hashable
from datetime import date, datetime
from typing import Any

from digest.config import AppConfig
from digest.metrics.cockpit import manager_cockpit_table
from digest.metrics.frame import prepare_lead_frame
from digest.metrics.kpi import Period
from factories import BUCHAREST, make_snapshot_row

SEPTEMBER = Period(
    start=datetime(2026, 9, 1, tzinfo=BUCHAREST), end=datetime(2026, 10, 1, tzinfo=BUCHAREST)
)
IN_SEPTEMBER = datetime(2026, 9, 10, 12, 0, tzinfo=BUCHAREST)
DRAGOI_ID = 12


def test_one_row_per_active_manager(app_config: AppConfig) -> None:
    table = manager_cockpit_table(
        prepare_lead_frame([], app_config), SEPTEMBER, date(2026, 9, 30), app_config
    )

    active_ids = [manager.id for manager in app_config.managers.managers if manager.active]
    assert table["manager_id"].tolist() == active_ids


def test_table_has_no_provisional_columns(app_config: AppConfig) -> None:
    table = manager_cockpit_table(
        prepare_lead_frame([], app_config), SEPTEMBER, date(2026, 9, 30), app_config
    )

    assert not {"spi", "level", "recommendation"} & set(table.columns)


def dragoi_row(rows: list[dict[str, Any]], config: AppConfig) -> dict[Hashable, Any]:
    table = manager_cockpit_table(
        prepare_lead_frame(rows, config), SEPTEMBER, date(2026, 9, 30), config
    )
    row: dict[Hashable, Any] = table.set_index("manager_id").loc[DRAGOI_ID].to_dict()
    return row


def lost(lead_id: int, loss_reason: str) -> dict[str, Any]:
    return make_snapshot_row(
        lead_id=lead_id, created_at=IN_SEPTEMBER, category="LOST", loss_reason=loss_reason
    )


def test_lower_is_better_target_is_met_at_the_limit(app_config: AppConfig) -> None:
    rows = [
        lost(1, "BUGET"),
        *(make_snapshot_row(lead_id=i, created_at=IN_SEPTEMBER) for i in (2, 3, 4)),
    ]

    row = dragoi_row(rows, app_config)

    assert (row["plr"], row["plr_meets_target"]) == (0.25, True)


def test_lower_is_better_target_is_missed_above_the_limit(app_config: AppConfig) -> None:
    rows = [
        lost(1, "BUGET"),
        *(make_snapshot_row(lead_id=i, created_at=IN_SEPTEMBER) for i in (2, 3)),
    ]

    assert dragoi_row(rows, app_config)["plr_meets_target"] is False


def test_higher_is_better_target_is_missed_below_the_limit(app_config: AppConfig) -> None:
    rows = [lost(1, "NU_RASPUNS"), make_snapshot_row(lead_id=2, created_at=IN_SEPTEMBER)]

    assert dragoi_row(rows, app_config)["cdr_meets_target"] is False


def test_target_is_unknown_for_null_kpi(app_config: AppConfig) -> None:
    row = dragoi_row([], app_config)

    assert (row["scr"], row["scr_meets_target"]) == (None, None)
