from datetime import date
from typing import Any

import pandas as pd
import pytest
from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.config import AppConfig
from digest.db.lead_frame import SnapshotMissingError, load_lead_frame
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
                    tenant_id="sofabelle", snapshot_date=SNAPSHOT_DATE, attempt=1, status=run_status
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
        make_snapshot_row(lead_id=1, created_by_id=8),
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
