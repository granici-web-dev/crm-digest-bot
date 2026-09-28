import asyncio
from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime, time
from typing import Any

import pytest
import respx
from pydantic import SecretStr
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.app import (
    catch_up_on_startup,
    missed_snapshot_job,
    report_missed_snapshots,
    seed_defaults,
    stored_schedules,
    take_late_snapshot,
)
from digest.config import AppConfig
from digest.db.schema import lead_snapshots, report_runs, snapshot_runs
from digest.delivery.ops import OpsChannel
from digest.mefi.client import MefiClient, RequestPacer, create_mefi_http_client
from digest.reports.modules import IMPLEMENTED_MODULES
from digest.reports.runner import ReportDeps, run_report
from digest.snapshot import SnapshotSources
from factories import (
    BUCHAREST,
    TEST_CONTACT_HASH_KEY,
    lead_snapshots_row,
    make_lead_links,
    make_search_page,
    make_snapshot_row,
    recorded_search_clients,
    recorded_search_leads,
)
from fakes import recording_bot

TENANT_ID = "sofabelle"
GROUP_CHAT_ID = -1001
OPS_CHAT_ID = -1003
BASE_URL = "https://mefi.test/api/v1"
LATE_LINE = "Raport trimis cu întârziere"
SUNDAY = date(2026, 9, 27)
MONDAY_MORNING = datetime(2026, 9, 28, 10, 0, tzinfo=BUCHAREST)


class Harness:
    def __init__(self, engine: AsyncEngine, config: AppConfig) -> None:
        report_bot, self.group = recording_bot()
        ops_bot, self.ops = recording_bot()
        self.deps = ReportDeps(
            engine=engine,
            config=config,
            tenant_id=TENANT_ID,
            report_bot=report_bot,
            ops=OpsChannel(ops_bot, OPS_CHAT_ID),
            report_chat_id=GROUP_CHAT_ID,
            modules=IMPLEMENTED_MODULES,
            lead_links=make_lead_links(config.status_mapping),
        )

    @property
    def group_text(self) -> str:
        return "\n".join(message.text for message in self.group.sent)

    @property
    def ops_texts(self) -> list[str]:
        return [message.text for message in self.ops.sent]


async def no_wait(seconds: float) -> None:
    return None


@pytest.fixture
async def harness(engine: AsyncEngine, app_config: AppConfig) -> Harness:
    await seed_defaults(engine, app_config, TENANT_ID)
    return Harness(engine, app_config)


@pytest.fixture
def mefi_mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
async def snapshot_sources() -> AsyncIterator[SnapshotSources]:
    async with create_mefi_http_client(BASE_URL, SecretStr("test-key")) as http_client:
        client = MefiClient(http_client, pacer=RequestPacer(1.2, sleep=no_wait))
        yield SnapshotSources(
            leads_client=client, clients_client=client, contact_hash_key=TEST_CONTACT_HASH_KEY
        )


def mefi_returns_recorded_data(mefi_mock: respx.MockRouter) -> respx.Route:
    mefi_mock.post(f"{BASE_URL}/clients/search").respond(
        json=make_search_page(recorded_search_clients())
    )
    return mefi_mock.post(f"{BASE_URL}/leads/search").respond(
        json=make_search_page(recorded_search_leads())
    )


async def store_success_snapshot(
    engine: AsyncEngine, snapshot_date: date, started_at: datetime | None = None
) -> None:
    created_at = datetime.combine(snapshot_date, time(11, 0), tzinfo=BUCHAREST)
    row = make_snapshot_row(lead_id=1, created_at=created_at)
    async with engine.begin() as connection:
        await connection.execute(
            insert(lead_snapshots).values(
                tenant_id=TENANT_ID, snapshot_date=snapshot_date, **lead_snapshots_row(row)
            )
        )
        await connection.execute(
            insert(snapshot_runs).values(
                tenant_id=TENANT_ID,
                snapshot_date=snapshot_date,
                attempt=1,
                status="success",
                started_at=started_at
                or datetime.combine(snapshot_date, time(19, 0), tzinfo=BUCHAREST),
            )
        )


async def snapshot_run_rows(engine: AsyncEngine) -> list[dict[str, Any]]:
    async with engine.connect() as connection:
        result = await connection.execute(
            select(snapshot_runs).order_by(snapshot_runs.c.snapshot_date, snapshot_runs.c.id)
        )
        return [dict(row) for row in result.mappings()]


async def report_run_rows(engine: AsyncEngine) -> list[dict[str, Any]]:
    async with engine.connect() as connection:
        result = await connection.execute(select(report_runs).order_by(report_runs.c.id))
        return [dict(row) for row in result.mappings()]


async def catch_up(harness: Harness, sources: SnapshotSources, now: datetime) -> None:
    schedules_by_level = await stored_schedules(harness.deps.engine, TENANT_ID)
    await catch_up_on_startup(harness.deps, sources, now, schedules_by_level)


async def test_late_snapshot_is_not_taken_when_today_success_exists(
    harness: Harness, snapshot_sources: SnapshotSources, mefi_mock: respx.MockRouter
) -> None:
    await store_success_snapshot(harness.deps.engine, date(2026, 9, 28))
    leads_route = mefi_returns_recorded_data(mefi_mock)

    await take_late_snapshot(
        harness.deps, snapshot_sources, datetime(2026, 9, 28, 21, 47, tzinfo=BUCHAREST)
    )

    assert leads_route.call_count == 0
    assert [row["status"] for row in await snapshot_run_rows(harness.deps.engine)] == ["success"]
    assert harness.ops_texts == []


async def test_late_snapshot_is_taken_after_retry_time_and_announced(
    harness: Harness, snapshot_sources: SnapshotSources, mefi_mock: respx.MockRouter
) -> None:
    mefi_returns_recorded_data(mefi_mock)

    await take_late_snapshot(
        harness.deps, snapshot_sources, datetime(2026, 9, 28, 21, 47, tzinfo=BUCHAREST)
    )

    [run] = await snapshot_run_rows(harness.deps.engine)
    assert (run["snapshot_date"], run["status"]) == (date(2026, 9, 28), "success")
    assert harness.ops_texts == ["Снапшот за 28.09.2026 снят с опозданием в 21:47."]


async def test_late_snapshot_waits_for_fresh_running_row(
    harness: Harness, snapshot_sources: SnapshotSources, mefi_mock: respx.MockRouter
) -> None:
    leads_route = mefi_returns_recorded_data(mefi_mock)
    async with harness.deps.engine.begin() as connection:
        await connection.execute(
            insert(snapshot_runs).values(
                tenant_id=TENANT_ID,
                snapshot_date=date(2026, 9, 28),
                attempt=1,
                status="running",
                started_at=text("now() - interval '2 minutes'"),
            )
        )

    await take_late_snapshot(
        harness.deps, snapshot_sources, datetime(2026, 9, 28, 21, 47, tzinfo=BUCHAREST)
    )

    assert leads_route.call_count == 0
    assert [row["status"] for row in await snapshot_run_rows(harness.deps.engine)] == ["running"]


async def test_startup_after_midnight_closes_yesterday_as_missed_without_snapshot(
    harness: Harness, snapshot_sources: SnapshotSources, mefi_mock: respx.MockRouter
) -> None:
    await store_success_snapshot(harness.deps.engine, date(2026, 9, 25))
    leads_route = mefi_returns_recorded_data(mefi_mock)

    await catch_up(harness, snapshot_sources, datetime(2026, 9, 27, 0, 30, tzinfo=BUCHAREST))
    await report_missed_snapshots(harness.deps, date(2026, 9, 27))

    assert leads_route.call_count == 0
    rows = await snapshot_run_rows(harness.deps.engine)
    assert [(row["snapshot_date"], row["status"], row["error"]) for row in rows] == [
        (date(2026, 9, 25), "success", None),
        (date(2026, 9, 26), "failed", "missed"),
    ]
    assert harness.ops_texts == ["Снапшот за 26.09.2026 пропущен, данные дня не восстановить."]
    assert harness.group.sent == []


async def test_missed_snapshot_check_is_silent_when_yesterday_snapshot_exists(
    harness: Harness,
) -> None:
    await store_success_snapshot(harness.deps.engine, date(2026, 9, 27))

    await report_missed_snapshots(harness.deps, date(2026, 9, 28))

    assert harness.ops_texts == []
    assert len(await snapshot_run_rows(harness.deps.engine)) == 1


async def test_missed_snapshot_is_reported_once(harness: Harness) -> None:
    await store_success_snapshot(harness.deps.engine, date(2026, 9, 25))

    await report_missed_snapshots(harness.deps, date(2026, 9, 28))
    await report_missed_snapshots(harness.deps, date(2026, 9, 28))

    assert harness.ops_texts == [
        "Снапшоты за 26.09.2026, 27.09.2026 пропущены, данные этих дней не восстановить."
    ]
    missed = [row for row in await snapshot_run_rows(harness.deps.engine) if row["error"]]
    assert [row["snapshot_date"] for row in missed] == [date(2026, 9, 26), date(2026, 9, 27)]


async def test_missed_snapshot_job_is_silent_on_fresh_database(harness: Harness) -> None:
    await missed_snapshot_job(harness.deps)

    assert harness.ops_texts == []


async def test_catch_up_does_not_resend_report_already_sent(
    harness: Harness, snapshot_sources: SnapshotSources
) -> None:
    await store_success_snapshot(harness.deps.engine, SUNDAY)
    await run_report(
        "weekly", datetime(2026, 9, 28, 9, 0, tzinfo=BUCHAREST), harness.deps, late=False
    )
    sent_before = len(harness.group.sent)
    ops_before = len(harness.ops_texts)

    await catch_up(harness, snapshot_sources, MONDAY_MORNING)

    assert len(harness.group.sent) == sent_before
    assert len(harness.ops_texts) == ops_before
    assert len(await report_run_rows(harness.deps.engine)) == 1


async def test_catch_up_sends_missed_weekly_with_late_line(
    harness: Harness, snapshot_sources: SnapshotSources
) -> None:
    await store_success_snapshot(harness.deps.engine, SUNDAY)

    await catch_up(harness, snapshot_sources, MONDAY_MORNING)

    assert harness.group.sent[0].text.splitlines()[0] == LATE_LINE
    assert "Raport săptămânal" in harness.group_text
    [run] = await report_run_rows(harness.deps.engine)
    assert (run["report_level"], run["status"]) == ("weekly", "success")


async def test_catch_up_skips_weekly_after_catch_up_days(
    harness: Harness, snapshot_sources: SnapshotSources
) -> None:
    await store_success_snapshot(harness.deps.engine, date(2026, 9, 20))

    await catch_up(harness, snapshot_sources, datetime(2026, 9, 24, 10, 0, tzinfo=BUCHAREST))

    assert harness.group.sent == []
    assert await report_run_rows(harness.deps.engine) == []


async def test_daily_catch_up_needs_today_success_snapshot(
    harness: Harness, snapshot_sources: SnapshotSources, mefi_mock: respx.MockRouter
) -> None:
    await store_success_snapshot(harness.deps.engine, date(2026, 9, 27))
    mefi_mock.post(f"{BASE_URL}/leads/search").respond(status_code=500)

    await catch_up(harness, snapshot_sources, datetime(2026, 9, 28, 21, 0, tzinfo=BUCHAREST))

    daily_runs = [
        run for run in await report_run_rows(harness.deps.engine) if run["report_level"] == "daily"
    ]
    assert daily_runs == []
    assert "Raport zilnic" not in harness.group_text
    assert any("Снапшот за 28.09.2026 21:00 не удался" in alert for alert in harness.ops_texts)


async def test_daily_catch_up_after_late_snapshot_marks_both(
    harness: Harness, snapshot_sources: SnapshotSources, mefi_mock: respx.MockRouter
) -> None:
    await store_success_snapshot(harness.deps.engine, date(2026, 9, 27))
    mefi_returns_recorded_data(mefi_mock)

    await catch_up(harness, snapshot_sources, datetime(2026, 9, 28, 21, 47, tzinfo=BUCHAREST))

    [daily_text] = [
        message.text for message in harness.group.sent if "Raport zilnic" in message.text
    ]
    lines = daily_text.splitlines()
    assert lines[0] == LATE_LINE
    assert any(line.startswith("Date extrase din mefi la ") for line in lines)
    runs = await report_run_rows(harness.deps.engine)
    assert "daily" in [run["report_level"] for run in runs]


async def test_on_time_daily_report_has_no_late_snapshot_line(harness: Harness) -> None:
    await store_success_snapshot(
        harness.deps.engine,
        date(2026, 9, 28),
        started_at=datetime(2026, 9, 28, 19, 10, tzinfo=BUCHAREST),
    )

    await run_report(
        "daily", datetime(2026, 9, 28, 19, 30, tzinfo=BUCHAREST), harness.deps, late=False
    )

    assert "Date extrase din mefi" not in harness.group_text
    assert LATE_LINE not in harness.group_text


async def test_weekly_report_ignores_late_sunday_snapshot(harness: Harness) -> None:
    await store_success_snapshot(
        harness.deps.engine, SUNDAY, started_at=datetime(2026, 9, 27, 22, 0, tzinfo=BUCHAREST)
    )

    await run_report(
        "weekly", datetime(2026, 9, 28, 9, 0, tzinfo=BUCHAREST), harness.deps, late=False
    )

    assert "Date extrase din mefi" not in harness.group_text


async def test_two_concurrent_catch_ups_send_once(
    engine: AsyncEngine, app_config: AppConfig, snapshot_sources: SnapshotSources
) -> None:
    await seed_defaults(engine, app_config, TENANT_ID)
    await store_success_snapshot(engine, date(2026, 9, 25))
    first, second = Harness(engine, app_config), Harness(engine, app_config)

    await asyncio.gather(
        catch_up(first, snapshot_sources, MONDAY_MORNING),
        catch_up(second, snapshot_sources, MONDAY_MORNING),
    )

    reports = [
        message
        for harness in (first, second)
        for message in harness.group.sent
        if message.text.startswith(LATE_LINE)
    ]
    assert len(reports) == 1
    # Воскресного снапшота нет: недельный уходит с пометкой «date mefi indisponibile».
    [run] = await report_run_rows(engine)
    assert (run["report_level"], run["status"]) == ("weekly", "partial")
    missed_alerts = [
        alert for harness in (first, second) for alert in harness.ops_texts if "пропущен" in alert
    ]
    assert missed_alerts == [
        "Снапшоты за 26.09.2026, 27.09.2026 пропущены, данные этих дней не восстановить."
    ]
