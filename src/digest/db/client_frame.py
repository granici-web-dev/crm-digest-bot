from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.config import AppConfig
from digest.db.schema import client_snapshots, snapshot_runs
from digest.metrics.frame import CLIENT_FRAME_COLUMNS, prepare_client_frame


async def load_client_frame(
    engine: AsyncEngine, tenant_id: str, snapshot_date: date, config: AppConfig
) -> pd.DataFrame | None:
    # None, а не пустой кадр: ноль контрактов вместо «данные недоступны» это неверная цифра.
    successful_clients_run = select(snapshot_runs.c.id).where(
        snapshot_runs.c.tenant_id == tenant_id,
        snapshot_runs.c.snapshot_date == snapshot_date,
        snapshot_runs.c.status == "success",
        snapshot_runs.c.clients_status == "success",
    )
    query = (
        select(*(client_snapshots.c[column] for column in CLIENT_FRAME_COLUMNS))
        .where(
            client_snapshots.c.tenant_id == tenant_id,
            client_snapshots.c.snapshot_date == snapshot_date,
        )
        .order_by(client_snapshots.c.client_id)
    )
    async with engine.connect() as connection:
        if (await connection.execute(successful_clients_run)).first() is None:
            return None
        result = await connection.execute(query)
        rows = [dict(row) for row in result.mappings()]
    return prepare_client_frame(rows, config)
