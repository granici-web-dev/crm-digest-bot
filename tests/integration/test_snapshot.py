from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.config import AppConfig
from digest.db.schema import lead_snapshots, snapshot_runs
from digest.mefi.client import MefiClient, RequestPacer, create_mefi_http_client
from digest.snapshot import run_daily_snapshot
from factories import make_lead, make_search_page, recorded_search_leads

BASE_URL = "https://mefi.test/api/v1"
SEARCH_URL = f"{BASE_URL}/leads/search"
SEPTEMBER_23_EVENING = datetime(2026, 9, 23, 16, 0, tzinfo=UTC)
SEPTEMBER_24_EVENING = datetime(2026, 9, 24, 16, 0, tzinfo=UTC)


async def no_wait(seconds: float) -> None:
    return None


@pytest.fixture
def mefi_mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
async def mefi_client() -> AsyncIterator[MefiClient]:
    async with create_mefi_http_client(BASE_URL, SecretStr("test-key")) as http_client:
        yield MefiClient(http_client, pacer=RequestPacer(1.2, sleep=no_wait), sleep=no_wait)


def mefi_returns(mefi_mock: respx.MockRouter, leads: list[dict[str, Any]]) -> respx.Route:
    return mefi_mock.post(SEARCH_URL).respond(json=make_search_page(leads))


async def snapshot(
    engine: AsyncEngine, mefi_client: MefiClient, app_config: AppConfig, now: datetime
) -> int:
    return await run_daily_snapshot(
        engine, mefi_client, app_config.status_mapping, "sofabelle", now
    )


async def run_row(engine: AsyncEngine, run_id: int) -> Any:
    async with engine.connect() as connection:
        return (
            await connection.execute(select(snapshot_runs).where(snapshot_runs.c.id == run_id))
        ).one()


async def snapshot_lead_ids(engine: AsyncEngine, snapshot_date: date) -> list[int]:
    async with engine.connect() as connection:
        return sorted(
            (
                await connection.execute(
                    select(lead_snapshots.c.lead_id).where(
                        lead_snapshots.c.snapshot_date == snapshot_date
                    )
                )
            ).scalars()
        )


async def test_snapshot_writes_rows_and_success_run(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    broken = make_lead(id=4000)
    del broken["created_at"]
    mefi_returns(
        mefi_mock,
        [*recorded_search_leads(), make_lead(id=4001, status=None), broken],
    )

    run_id = await snapshot(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    run = await run_row(engine, run_id)
    assert (run.status, run.attempt, run.snapshot_date) == ("success", 1, date(2026, 9, 24))
    assert (run.api_total, run.leads_written, run.unmapped_count, run.skipped_count) == (5, 4, 1, 1)
    assert run.skipped_leads == [{"lead_id": 4000, "reason": "created_at: missing"}]
    assert run.new_unmapped_lead_ids == [4001]
    assert run.missing_since_previous is None
    assert run.custom_field_mismatches == []
    assert await snapshot_lead_ids(engine, date(2026, 9, 24)) == [3026, 3027, 3028, 4001]
    async with engine.connect() as connection:
        raw = (
            await connection.execute(
                select(lead_snapshots.c.raw).where(lead_snapshots.c.lead_id == 3028)
            )
        ).scalar_one()
    assert "phone" not in raw
    assert raw["location"]["city"] == "Bacau"


async def test_failed_snapshot_leaves_no_rows_and_failed_run(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    mefi_mock.post(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=make_search_page([make_lead(id=1)], page=1, total_pages=2)),
            httpx.Response(503),
        ]
    )

    with pytest.raises(httpx.HTTPStatusError):
        await snapshot(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    async with engine.connect() as connection:
        run = (await connection.execute(select(snapshot_runs))).one()
    assert run.status == "failed"
    assert run.error.startswith("HTTPStatusError")
    assert run.finished_at is not None
    assert await snapshot_lead_ids(engine, date(2026, 9, 24)) == []


async def test_second_call_same_date_after_success_is_noop(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    route = mefi_returns(mefi_mock, [make_lead(id=1)])

    first_run_id = await snapshot(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)
    second_run_id = await snapshot(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    assert second_run_id == first_run_id
    assert route.call_count == 1


async def test_retry_after_failure_is_second_attempt(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    mefi_mock.post(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(500),
            httpx.Response(200, json=make_search_page([make_lead(id=1)])),
        ]
    )
    with pytest.raises(httpx.HTTPStatusError):
        await snapshot(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    run_id = await snapshot(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    run = await run_row(engine, run_id)
    assert (run.status, run.attempt) == ("success", 2)


async def test_new_unmapped_ids_only_on_first_appearance(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    unknown_status = {"id": 99, "name": "STATUS NOU"}
    mefi_mock.post(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=make_search_page([make_lead(id=1, status=unknown_status)])),
            httpx.Response(
                200,
                json=make_search_page(
                    [make_lead(id=1, status=unknown_status), make_lead(id=2, status=None)]
                ),
            ),
        ]
    )

    first_run_id = await snapshot(engine, mefi_client, app_config, SEPTEMBER_23_EVENING)
    second_run_id = await snapshot(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    assert (await run_row(engine, first_run_id)).new_unmapped_lead_ids == [1]
    second_run = await run_row(engine, second_run_id)
    assert second_run.new_unmapped_lead_ids == [2]
    assert second_run.unmapped_count == 2


async def test_missing_since_previous_counts_disappeared_leads(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    mefi_mock.post(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(
                200, json=make_search_page([make_lead(id=1), make_lead(id=2), make_lead(id=3)])
            ),
            httpx.Response(200, json=make_search_page([make_lead(id=1), make_lead(id=4)])),
        ]
    )

    await snapshot(engine, mefi_client, app_config, SEPTEMBER_23_EVENING)
    run_id = await snapshot(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    run = await run_row(engine, run_id)
    assert run.previous_snapshot_date == date(2026, 9, 23)
    assert run.missing_since_previous == 2


async def test_won_status_without_converted_at_is_recorded(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    mefi_returns(
        mefi_mock,
        [
            make_lead(id=1, status={"id": 1, "name": "Clienți"}, converted_at=None),
            make_lead(
                id=2, status={"id": 1, "name": "Clienți"}, converted_at="2026-09-20T10:00:00Z"
            ),
            make_lead(id=3, converted_at="2026-09-20T10:00:00Z"),
        ],
    )

    run_id = await snapshot(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    assert (await run_row(engine, run_id)).won_converted_mismatch_ids == [1, 3]


async def test_snapshot_date_is_bucharest_date(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    mefi_returns(mefi_mock, [make_lead(id=1)])

    run_id = await snapshot(
        engine, mefi_client, app_config, datetime(2026, 9, 24, 23, 30, tzinfo=UTC)
    )

    assert (await run_row(engine, run_id)).snapshot_date == date(2026, 9, 25)
