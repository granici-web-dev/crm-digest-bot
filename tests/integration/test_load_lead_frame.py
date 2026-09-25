from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy import insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.config import AppConfig
from digest.db.schema import lead_snapshots, tenants
from digest.metrics.frame import load_lead_frame, prepare_lead_frame
from factories import make_snapshot_row

SNAPSHOT_DATE = date(2026, 9, 24)


def stored(row: dict[str, Any], tenant_id: str = "sofabelle", **keys: Any) -> dict[str, Any]:
    return {"tenant_id": tenant_id, "snapshot_date": SNAPSHOT_DATE, "raw": {}, **row, **keys}


async def store(engine: AsyncEngine, rows: list[dict[str, Any]]) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            pg_insert(tenants).values(id="other", name="Other").on_conflict_do_nothing()
        )
        await connection.execute(insert(lead_snapshots), rows)


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
        make_snapshot_row(lead_id=1),
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
