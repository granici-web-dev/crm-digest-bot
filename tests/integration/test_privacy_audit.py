from datetime import timedelta
from typing import Any

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncEngine
from test_report_runner import REPORT_DATE, Harness, store_snapshot, todays_lead

from digest.acceptance.privacy import audit_privacy
from digest.config import AppConfig
from digest.db.schema import lead_snapshots


async def set_raw(engine: AsyncEngine, lead_id: int, raw: dict[str, Any]) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            update(lead_snapshots).where(lead_snapshots.c.lead_id == lead_id).values(raw=raw)
        )


async def test_clean_snapshot_passes_audit(engine: AsyncEngine, app_config: AppConfig) -> None:
    harness = Harness(engine, app_config)
    await store_snapshot(engine, REPORT_DATE, [todays_lead(1), todays_lead(2)])

    audit = await audit_privacy(harness.deps.reader, REPORT_DATE, days=2)

    assert audit.checked_reports == 1
    assert audit.dates_without_snapshot == [REPORT_DATE - timedelta(days=1)]
    assert audit.findings == []
    assert audit.raw_findings == []


async def test_stripped_key_and_dropped_custom_field_in_raw_are_found(
    engine: AsyncEngine, app_config: AppConfig
) -> None:
    harness = Harness(engine, app_config)
    await store_snapshot(engine, REPORT_DATE, [todays_lead(1), todays_lead(2), todays_lead(3)])
    await set_raw(engine, 1, {"phone": "+40700000001"})
    await set_raw(engine, 2, {"location": {"address_line": "Strada Test 1"}})
    await set_raw(engine, 3, {"custom_fields": [{"field_id": 7, "value": "NOTA_CLIENT_TEST"}]})

    audit = await audit_privacy(harness.deps.reader, REPORT_DATE, days=1)

    assert {(finding.table, finding.check, finding.rows) for finding in audit.raw_findings} == {
        ("lead_snapshots", "ключи raw_strip", 2),
        ("lead_snapshots", "custom_fields вне keep", 1),
    }
