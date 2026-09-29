from dataclasses import replace
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from test_report_runner import NOW, REPORT_DATE, Harness, store_snapshot, todays_lead

from conftest import TABLES_TRUNCATED_BETWEEN_TESTS
from digest.config import AppConfig
from digest.db.schema import metadata
from digest.reports.context import ReportContext
from digest.reports.modules import IMPLEMENTED_MODULES
from digest.reports.periods import report_period
from digest.reports.runner import (
    load_report_inputs,
    render_report,
    report_snapshot_date,
    run_report,
    select_modules,
)


async def table_contents(engine: AsyncEngine) -> dict[str, list[tuple[Any, ...]]]:
    async with engine.connect() as connection:
        return {
            table: [tuple(row) for row in await connection.execute(select(metadata.tables[table]))]
            for table in TABLES_TRUNCATED_BETWEEN_TESTS
        }


async def store_snapshot_with_findings(harness: Harness) -> None:
    await store_snapshot(
        harness.deps.engine,
        REPORT_DATE,
        [
            todays_lead(7, category="UNMAPPED", status_name="STATUS NOU", assigned_to_id=999),
            todays_lead(3, category="WON", status_name="Clienți"),
        ],
        new_unmapped_lead_ids=[7],
        won_converted_mismatch_ids=[3],
    )


def failing_module(lead_frame: pd.DataFrame, context: ReportContext) -> Any:
    raise ValueError("boom")


async def test_report_build_changes_no_rows_and_sends_nothing(
    engine: AsyncEngine, app_config: AppConfig
) -> None:
    harness = Harness(engine, app_config)
    await store_snapshot_with_findings(harness)
    reader = replace(harness.deps, modules={**IMPLEMENTED_MODULES, "d2": failing_module}).reader
    time_settings = app_config.status_mapping.time
    period = report_period("daily", NOW, time_settings)
    snapshot_date = report_snapshot_date(period, ZoneInfo(time_settings.timezone))
    before = await table_contents(engine)

    selection = await select_modules(reader, "daily")
    module_ids = {module_id for module_id, _ in selection.runnable}
    inputs = await load_report_inputs(reader, "daily", snapshot_date, module_ids)
    report = render_report(
        app_config, "daily", period, snapshot_date, False, selection.runnable, inputs
    )

    assert await table_contents(engine) == before
    assert harness.group.sent == []
    assert harness.ops.sent == []
    assert snapshot_date == REPORT_DATE
    assert [block.module_id for block in report.blocks] == [
        module_id for module_id, _ in selection.runnable
    ]
    assert "Модуль d2 отчёта daily упал: ValueError." in report.alerts


async def test_missing_snapshot_gives_no_inputs(engine: AsyncEngine, app_config: AppConfig) -> None:
    harness = Harness(engine, app_config)

    inputs = await load_report_inputs(harness.deps.reader, "daily", REPORT_DATE, {"d1"})

    assert inputs is None


async def test_run_report_sends_findings_before_module_alerts(
    engine: AsyncEngine, app_config: AppConfig
) -> None:
    harness = Harness(engine, app_config)
    await store_snapshot_with_findings(harness)
    deps = replace(harness.deps, modules={**IMPLEMENTED_MODULES, "d2": failing_module})

    await run_report("daily", NOW, deps, late=False)

    alerts = harness.ops_texts
    findings_index = alerts.index(
        "Новые лиды с неизвестным статусом «STATUS NOU», UNMAPPED (1), id: [7]. "
        "Добавьте статус в config/status-mapping.yaml."
    )
    module_index = alerts.index("Модуль d2 отчёта daily упал: ValueError.")
    assert findings_index < module_index
    assert "sursa B indisponibilă" in harness.group_text
