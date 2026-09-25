import logging
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.app import DEFAULT_SCHEDULES, seed_defaults
from digest.config import AppConfig
from digest.db.schema import lead_snapshots, module_settings, report_runs, schedules, snapshot_runs
from digest.delivery.ops import OpsChannel
from digest.reports.context import ModuleResult, ReportContext
from digest.reports.modules import IMPLEMENTED_MODULES
from digest.reports.runner import ReportDeps, run_report
from factories import BUCHAREST, make_snapshot_row
from fakes import recording_bot

TENANT_ID = "sofabelle"
GROUP_CHAT_ID = -1001
TEST_CHAT_ID = -1002
OPS_CHAT_ID = -1003
REPORT_DATE = date(2026, 9, 25)
NOW = datetime(2026, 9, 25, 19, 30, tzinfo=BUCHAREST)


class Harness:
    def __init__(
        self, engine: AsyncEngine, config: AppConfig, report_chat_id: int = GROUP_CHAT_ID
    ) -> None:
        report_bot, self.group = recording_bot()
        ops_bot, self.ops = recording_bot()
        self.deps = ReportDeps(
            engine=engine,
            config=config,
            tenant_id=TENANT_ID,
            report_bot=report_bot,
            ops=OpsChannel(ops_bot, OPS_CHAT_ID),
            report_chat_id=report_chat_id,
            modules=IMPLEMENTED_MODULES,
        )

    @property
    def group_text(self) -> str:
        return "\n".join(message.text for message in self.group.sent)

    @property
    def ops_texts(self) -> list[str]:
        return [message.text for message in self.ops.sent]


@pytest.fixture
async def harness(engine: AsyncEngine, app_config: AppConfig) -> Harness:
    await seed_defaults(engine, TENANT_ID)
    return Harness(engine, app_config)


async def store_snapshot(
    engine: AsyncEngine, snapshot_date: date, rows: list[dict[str, Any]], **run_values: Any
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            insert(lead_snapshots),
            [
                {"tenant_id": TENANT_ID, "snapshot_date": snapshot_date, "raw": {}, **row}
                for row in rows
            ],
        )
        await connection.execute(
            insert(snapshot_runs).values(
                tenant_id=TENANT_ID,
                snapshot_date=snapshot_date,
                attempt=1,
                status="success",
                **run_values,
            )
        )


def todays_lead(lead_id: int, **overrides: Any) -> dict[str, Any]:
    created_at = datetime(2026, 9, 25, 11, 0, tzinfo=BUCHAREST)
    return make_snapshot_row(**{"lead_id": lead_id, "created_at": created_at, **overrides})


async def insert_run(
    engine: AsyncEngine,
    status: str,
    started_minutes_ago: int = 0,
    message_ids: list[int] | None = None,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            insert(report_runs).values(
                tenant_id=TENANT_ID,
                report_level="daily",
                period_start=datetime(2026, 9, 24, 19, tzinfo=BUCHAREST),
                period_end=datetime(2026, 9, 25, 19, tzinfo=BUCHAREST),
                chat_id=GROUP_CHAT_ID,
                status=status,
                started_at=text(f"now() - interval '{started_minutes_ago} minutes'"),
                message_ids=message_ids,
            )
        )


async def report_run_rows(engine: AsyncEngine) -> list[dict[str, Any]]:
    async with engine.connect() as connection:
        result = await connection.execute(select(report_runs).order_by(report_runs.c.id))
        return [dict(row) for row in result.mappings()]


async def test_daily_report_is_sent_to_group_and_recorded(harness: Harness) -> None:
    await store_snapshot(harness.deps.engine, REPORT_DATE, [todays_lead(1, source_name="Telefon")])

    outcome = await run_report("daily", NOW, harness.deps)

    assert outcome == "success"
    assert {message.chat_id for message in harness.group.sent} == {GROUP_CHAT_ID}
    assert "<b>Raport zilnic Sofabelle</b>" in harness.group_text
    assert "24.09.2026 19:00 → 25.09.2026 19:00" in harness.group_text
    assert "<b>Sofabelle București:</b>" in harness.group_text
    assert harness.group_text.count("sursa B indisponibilă") == 1
    [run] = await report_run_rows(harness.deps.engine)
    assert run["status"] == "success"
    assert run["snapshot_date"] == REPORT_DATE
    assert run["message_ids"] == [message.message_id for message in harness.group.sent]


async def test_second_run_for_same_period_sends_nothing(harness: Harness) -> None:
    await store_snapshot(harness.deps.engine, REPORT_DATE, [todays_lead(1)])
    await run_report("daily", NOW, harness.deps)
    sent_before = len(harness.group.sent)

    outcome = await run_report("daily", NOW + timedelta(hours=1), harness.deps)

    assert outcome == "already_sent"
    assert len(harness.group.sent) == sent_before


async def test_failed_run_is_sent_again(harness: Harness) -> None:
    await store_snapshot(harness.deps.engine, REPORT_DATE, [todays_lead(1)])
    await insert_run(harness.deps.engine, status="failed")

    outcome = await run_report("daily", NOW, harness.deps)

    assert outcome == "success"
    assert harness.group.sent
    [run] = await report_run_rows(harness.deps.engine)
    assert run["status"] == "success"


async def test_stale_running_run_is_resent_with_alert(harness: Harness) -> None:
    await store_snapshot(harness.deps.engine, REPORT_DATE, [todays_lead(1)])
    await insert_run(harness.deps.engine, status="running", started_minutes_ago=31)

    outcome = await run_report("daily", NOW, harness.deps)

    assert outcome == "success"
    assert harness.group.sent
    assert any("прерван, отчёт отправлен заново" in alert for alert in harness.ops_texts)


async def test_fresh_running_run_is_left_alone(harness: Harness) -> None:
    await store_snapshot(harness.deps.engine, REPORT_DATE, [todays_lead(1)])
    await insert_run(harness.deps.engine, status="running", started_minutes_ago=5)

    outcome = await run_report("daily", NOW, harness.deps)

    assert outcome == "in_progress"
    assert harness.group.sent == []
    assert any("уже отправляется другим прогоном" in alert for alert in harness.ops_texts)


async def test_failing_module_is_replaced_by_note_and_reported(harness: Harness) -> None:
    def failing_module(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
        raise KeyError("column")

    deps = replace(harness.deps, modules={**IMPLEMENTED_MODULES, "d2": failing_module})
    await store_snapshot(harness.deps.engine, REPORT_DATE, [todays_lead(1)])

    outcome = await run_report("daily", NOW, deps)

    assert outcome == "partial"
    assert "<b>Sofabelle București:</b>" in harness.group_text
    assert "Blocul d2 nu a putut fi calculat" in harness.group_text
    assert "Модуль d2 отчёта daily упал: KeyError." in harness.ops_texts


async def test_missing_snapshot_sends_report_with_note_and_alert(harness: Harness) -> None:
    outcome = await run_report("daily", NOW, harness.deps)

    assert outcome == "partial"
    assert "Date mefi indisponibile" in harness.group_text
    assert "Sofabelle București" not in harness.group_text
    assert any("отсутствует или failed" in alert for alert in harness.ops_texts)


async def test_test_chat_run_does_not_block_production_run(
    harness: Harness, app_config: AppConfig
) -> None:
    await store_snapshot(harness.deps.engine, REPORT_DATE, [todays_lead(1)])
    test_chat = Harness(harness.deps.engine, app_config, report_chat_id=TEST_CHAT_ID)
    await run_report("daily", NOW, test_chat.deps)

    outcome = await run_report("daily", NOW, harness.deps)

    assert outcome == "success"
    assert {message.chat_id for message in test_chat.group.sent} == {TEST_CHAT_ID}
    assert {message.chat_id for message in harness.group.sent} == {GROUP_CHAT_ID}


async def test_module_error_text_reaches_neither_logs_nor_alerts(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    client_phone = "+40700000001"

    def failing_module(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
        raise ValueError(f"lead {client_phone} Client Unu")

    deps = replace(harness.deps, modules={**IMPLEMENTED_MODULES, "d2": failing_module})
    await store_snapshot(harness.deps.engine, REPORT_DATE, [todays_lead(1)])

    with caplog.at_level(logging.DEBUG):
        await run_report("daily", NOW, deps)

    assert "Модуль d2 отчёта daily упал: ValueError." in harness.ops_texts
    assert client_phone not in caplog.text
    assert not any(client_phone in alert for alert in harness.ops_texts)


async def test_snapshot_findings_are_alerted_as_ids_only(harness: Harness) -> None:
    await store_snapshot(
        harness.deps.engine,
        REPORT_DATE,
        [todays_lead(7, category="UNMAPPED", status_name="STATUS NOU", assigned_to_id=999)],
        new_unmapped_lead_ids=[7],
        won_converted_mismatch_ids=[3],
    )

    await run_report("daily", NOW, harness.deps)

    assert (
        "Новые UNMAPPED лиды (1), id: [7]. Добавьте статус в config/status-mapping.yaml."
        in harness.ops_texts
    )
    assert "Clienți и converted_at расходятся (1), id: [3]." in harness.ops_texts
    assert (
        "Лиды на консультантах вне config/managers.yaml, assigned_to.id: [999]."
        in harness.ops_texts
    )


async def test_yesterdays_snapshot_gives_transitions(harness: Harness) -> None:
    yesterday = REPORT_DATE - timedelta(days=1)
    await store_snapshot(harness.deps.engine, yesterday, [todays_lead(1, ofertat=False)])
    await store_snapshot(harness.deps.engine, REPORT_DATE, [todays_lead(1, ofertat=True)])

    await run_report("daily", NOW, harness.deps)

    assert "Oferte 1" in harness.group_text


async def test_older_previous_snapshot_is_not_diffed(harness: Harness) -> None:
    two_days_ago = REPORT_DATE - timedelta(days=2)
    await store_snapshot(harness.deps.engine, two_days_ago, [todays_lead(1, ofertat=False)])
    await store_snapshot(harness.deps.engine, REPORT_DATE, [todays_lead(1, ofertat=True)])

    await run_report("daily", NOW, harness.deps)

    assert "Oferte —" in harness.group_text
    assert "lipsește snapshotul CRM de ieri" in harness.group_text


async def test_report_without_implemented_modules_is_not_sent(harness: Harness) -> None:
    await store_snapshot(harness.deps.engine, REPORT_DATE, [todays_lead(1)])

    outcome = await run_report("weekly", NOW, harness.deps)

    assert outcome == "failed"
    assert harness.group.sent == []
    assert any("нет ни одного реализованного модуля" in alert for alert in harness.ops_texts)


async def test_module_disabled_in_module_settings_is_skipped(harness: Harness) -> None:
    await store_snapshot(harness.deps.engine, REPORT_DATE, [todays_lead(1)])
    async with harness.deps.engine.begin() as connection:
        await connection.execute(
            insert(module_settings).values(tenant_id=TENANT_ID, module_id="d1", enabled=False)
        )

    outcome = await run_report("daily", NOW, harness.deps)

    assert outcome == "failed"
    assert harness.group.sent == []


async def test_seed_defaults_keeps_existing_values(engine: AsyncEngine) -> None:
    await seed_defaults(engine, TENANT_ID)
    async with engine.begin() as connection:
        await connection.execute(
            text("UPDATE schedules SET cron = '0 20 * * *' WHERE report_level = 'daily'")
        )

    await seed_defaults(engine, TENANT_ID)

    async with engine.connect() as connection:
        result = await connection.execute(select(schedules.c.report_level, schedules.c.cron))
        cron_by_level = {level: cron for level, cron in result}
    assert cron_by_level == {**DEFAULT_SCHEDULES, "daily": "0 20 * * *"}


async def test_send_failure_marks_run_failed_and_next_run_resends(harness: Harness) -> None:
    await store_snapshot(harness.deps.engine, REPORT_DATE, [todays_lead(1)])
    harness.group.fail_next(TelegramNetworkError(SendMessage(chat_id=1, text="x"), "down"))

    first_outcome = await run_report("daily", NOW, harness.deps)
    second_outcome = await run_report("daily", NOW, harness.deps)

    assert (first_outcome, second_outcome) == ("failed", "success")
    assert any("не удалась: TelegramNetworkError" in alert for alert in harness.ops_texts)
    assert harness.group.sent


async def test_module_alerts_are_sent_to_ops(harness: Harness) -> None:
    await store_snapshot(
        harness.deps.engine, REPORT_DATE, [todays_lead(4, source_name="Sursa noua")]
    )

    await run_report("daily", NOW, harness.deps)

    assert any(
        alert.startswith("d1: лиды с источником вне групп") and "id: [4]" in alert
        for alert in harness.ops_texts
    )


async def test_rerun_after_partial_send_keeps_earlier_message_ids(harness: Harness) -> None:
    await store_snapshot(harness.deps.engine, REPORT_DATE, [todays_lead(1)])
    await insert_run(harness.deps.engine, status="failed", message_ids=[501, 502])

    outcome = await run_report("daily", NOW, harness.deps)

    assert outcome == "success"
    [run] = await report_run_rows(harness.deps.engine)
    assert run["message_ids"] == [501, 502, *(message.message_id for message in harness.group.sent)]
    assert any(
        "Ранее отправлено частей: 2, в чате будет дубль" in alert for alert in harness.ops_texts
    )


async def test_weekly_report_on_monday_reads_sundays_snapshot(harness: Harness) -> None:
    sunday = date(2026, 9, 27)
    seen_report_dates: list[date] = []

    def weekly_module(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
        seen_report_dates.append(context.report_date)
        return ModuleResult(f"leads: {len(lead_frame)}")

    deps = replace(harness.deps, modules={"w1": weekly_module})
    await store_snapshot(harness.deps.engine, sunday, [todays_lead(1), todays_lead(2)])

    outcome = await run_report("weekly", datetime(2026, 9, 28, 9, 0, tzinfo=BUCHAREST), deps)

    assert outcome == "success"
    assert seen_report_dates == [sunday]
    assert "leads: 2" in harness.group_text
    [run] = await report_run_rows(harness.deps.engine)
    assert run["snapshot_date"] == sunday
