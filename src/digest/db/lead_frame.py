from datetime import date

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.config import AppConfig
from digest.db.schema import lead_snapshots, snapshot_runs
from digest.metrics.frame import LEAD_FRAME_COLUMNS, prepare_lead_frame


class SnapshotMissingError(Exception):
    pass


async def load_lead_frame(
    engine: AsyncEngine, tenant_id: str, snapshot_date: date, config: AppConfig
) -> pd.DataFrame:
    query = (
        select(*(lead_snapshots.c[column] for column in LEAD_FRAME_COLUMNS))
        .where(
            lead_snapshots.c.tenant_id == tenant_id,
            lead_snapshots.c.snapshot_date == snapshot_date,
        )
        .order_by(lead_snapshots.c.lead_id)
    )
    successful_run = select(snapshot_runs.c.id).where(
        snapshot_runs.c.tenant_id == tenant_id,
        snapshot_runs.c.snapshot_date == snapshot_date,
        snapshot_runs.c.status == "success",
    )
    async with engine.connect() as connection:
        has_successful_run = (await connection.execute(successful_run)).first() is not None
        result = await connection.execute(query)
        rows = [dict(row) for row in result.mappings()]
    # Пустой фрейм дал бы нули во всех счётчиках: неверная цифра вместо «данные недоступны».
    if not has_successful_run or not rows:
        raise SnapshotMissingError(
            f"снапшот {tenant_id} за {snapshot_date} отсутствует или неполный"
        )
    return prepare_lead_frame(rows, config)
