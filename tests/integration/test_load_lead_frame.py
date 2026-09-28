from datetime import date
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.config import AppConfig
from digest.db.lead_frame import SnapshotMissingError, load_lead_frame, success_snapshot_dates
from digest.db.schema import lead_snapshots, snapshot_runs, tenants
from digest.metrics.frame import prepare_lead_frame
from factories import lead_snapshots_row, make_snapshot_row

SNAPSHOT_DATE = date(2026, 9, 24)


def stored(row: dict[str, Any], tenant_id: str = "sofabelle", **keys: Any) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "snapshot_date": SNAPSHOT_DATE,
        **lead_snapshots_row(row),
        **keys,
    }


async def store(
    engine: AsyncEngine, rows: list[dict[str, Any]], run_status: str | None = "success"
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            pg_insert(tenants).values(id="other", name="Other").on_conflict_do_nothing()
        )
        if rows:
            await connection.execute(insert(lead_snapshots), rows)
        if run_status is not None:
            await connection.execute(
                insert(snapshot_runs).values(
                    tenant_id="sofabelle",
                    snapshot_date=SNAPSHOT_DATE,
                    attempt=1,
                    status=run_status,
                    trigger="scheduled",
                )
            )


async def test_loads_only_requested_tenant_and_date(
    engine: AsyncEngine, app_config: AppConfig
) -> None:
    await store(
        engine,
        [
            stored(make_snapshot_row(lead_id=1)),
            stored(make_snapshot_row(lead_id=2), snapshot_date=date(2026, 9, 23)),
            stored(make_snapshot_row(lead_id=3), tenant_id="other"),
        ],
    )

    lead_frame = await load_lead_frame(engine, "sofabelle", SNAPSHOT_DATE, app_config)

    assert lead_frame["lead_id"].tolist() == [1]


async def test_loaded_frame_matches_frame_prepared_from_rows(
    engine: AsyncEngine, app_config: AppConfig
) -> None:
    rows = [
        # created_by_id приходит из raw.created_by.id, у лида 2 его нет (лид из API).
        make_snapshot_row(lead_id=1, created_by_id=8, utm_campanie="BZA_Cluj_Website_Leads 03"),
        make_snapshot_row(
            lead_id=2,
            ofertat=None,
            assigned_to_id=None,
            showroom=None,
            data_revenire=date(2026, 9, 20),
            category="LOST",
            loss_reason="STAND_BY",
        ),
    ]
    await store(engine, [stored(row) for row in rows])

    lead_frame = await load_lead_frame(engine, "sofabelle", SNAPSHOT_DATE, app_config)

    pd.testing.assert_frame_equal(lead_frame, prepare_lead_frame(rows, app_config))


async def test_utm_campanie_is_read_from_raw_by_field_id(
    engine: AsyncEngine, app_config: AppConfig
) -> None:
    other_utm_field = {"field_id": 38, "name": "UTM_Source", "type": "input", "value": "facebook"}
    with_campaign = lead_snapshots_row(make_snapshot_row(lead_id=1, utm_campanie="BZA_Cluj <03>"))
    with_campaign["raw"]["custom_fields"].insert(0, other_utm_field)
    only_other_field = lead_snapshots_row(make_snapshot_row(lead_id=2))
    only_other_field["raw"]["custom_fields"] = [other_utm_field]
    await store(engine, [stored_raw(with_campaign), stored_raw(only_other_field)])

    lead_frame = await load_lead_frame(engine, "sofabelle", SNAPSHOT_DATE, app_config)

    assert lead_frame["utm_campanie"].iloc[0] == "BZA_Cluj <03>"
    assert pd.isna(lead_frame["utm_campanie"].iloc[1])


@pytest.mark.parametrize("value", ["", "   ", "\t\r\n\u00a0", None])
async def test_blank_campaign_is_null(
    engine: AsyncEngine, app_config: AppConfig, value: str | None
) -> None:
    row = lead_snapshots_row(make_snapshot_row(lead_id=1))
    row["raw"]["custom_fields"] = [
        {"field_id": 39, "name": "UTM_Campanie", "type": "input", "value": value}
    ]
    await store(engine, [stored_raw(row)])

    lead_frame = await load_lead_frame(engine, "sofabelle", SNAPSHOT_DATE, app_config)

    assert lead_frame["utm_campanie"].isna().all()


async def test_campaign_is_trimmed_of_tabs_line_breaks_and_nbsp(
    engine: AsyncEngine, app_config: AppConfig
) -> None:
    row = lead_snapshots_row(make_snapshot_row(lead_id=1))
    row["raw"]["custom_fields"] = [
        {"field_id": 39, "name": "UTM_Campanie", "type": "input", "value": "\u00a0\tBZA 03\r\n"}
    ]
    await store(engine, [stored_raw(row)])

    lead_frame = await load_lead_frame(engine, "sofabelle", SNAPSHOT_DATE, app_config)

    assert lead_frame["utm_campanie"].iloc[0] == "BZA 03"


def stored_raw(snapshot_row: dict[str, Any]) -> dict[str, Any]:
    return {"tenant_id": "sofabelle", "snapshot_date": SNAPSHOT_DATE, **snapshot_row}


@pytest.mark.parametrize(
    ("rows", "run_status"),
    [
        ([], None),
        ([stored(make_snapshot_row())], None),
        ([stored(make_snapshot_row())], "failed"),
        ([], "success"),
    ],
    ids=["nothing", "rows_without_run", "failed_run", "success_without_rows"],
)
async def test_missing_or_incomplete_snapshot_raises_instead_of_empty_frame(
    engine: AsyncEngine,
    app_config: AppConfig,
    rows: list[dict[str, Any]],
    run_status: str | None,
) -> None:
    await store(engine, rows, run_status=run_status)

    with pytest.raises(SnapshotMissingError):
        await load_lead_frame(engine, "sofabelle", SNAPSHOT_DATE, app_config)


async def test_success_snapshot_dates_are_sorted_successes_of_tenant(engine: AsyncEngine) -> None:
    runs = [
        ("sofabelle", date(2026, 9, 24), 1, "success"),
        ("sofabelle", date(2026, 9, 22), 1, "success"),
        ("sofabelle", date(2026, 9, 23), 1, "failed"),
        ("other", date(2026, 9, 21), 1, "success"),
    ]
    async with engine.begin() as connection:
        await connection.execute(
            pg_insert(tenants).values(id="other", name="Other").on_conflict_do_nothing()
        )
        for tenant_id, snapshot_date, attempt, status in runs:
            await connection.execute(
                insert(snapshot_runs).values(
                    tenant_id=tenant_id,
                    snapshot_date=snapshot_date,
                    attempt=attempt,
                    status=status,
                    trigger="scheduled",
                )
            )

    assert await success_snapshot_dates(engine, "sofabelle") == (
        date(2026, 9, 22),
        date(2026, 9, 24),
    )
