import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from pydantic import SecretStr, ValidationError
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.config import AppConfig
from digest.db.schema import client_snapshots, lead_snapshots, snapshot_runs
from digest.mefi.client import (
    MefiClient,
    MefiSearchUnsuccessful,
    RequestPacer,
    create_mefi_http_client,
)
from digest.snapshot import (
    SnapshotIncomplete,
    SnapshotOutcome,
    SnapshotSources,
    run_daily_snapshot,
)
from factories import (
    TEST_CONTACT_HASH_KEY,
    make_client,
    make_lead,
    make_search_page,
    recorded_search_clients,
    recorded_search_leads,
)

BASE_URL = "https://mefi.test/api/v1"
SEARCH_URL = f"{BASE_URL}/leads/search"
CLIENTS_SEARCH_URL = f"{BASE_URL}/clients/search"
BUCHAREST = ZoneInfo("Europe/Bucharest")
SEPTEMBER_23_EVENING = datetime(2026, 9, 23, 19, 0, tzinfo=BUCHAREST)
SEPTEMBER_24_EVENING = datetime(2026, 9, 24, 19, 0, tzinfo=BUCHAREST)


async def no_wait(seconds: float) -> None:
    return None


@pytest.fixture
def mefi_mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


@pytest.fixture
async def mefi_client() -> AsyncIterator[MefiClient]:
    async with create_mefi_http_client(BASE_URL, SecretStr("test-key")) as http_client:
        yield MefiClient(http_client, pacer=RequestPacer(1.2, sleep=no_wait))


def mefi_returns(mefi_mock: respx.MockRouter, leads: list[dict[str, Any]]) -> respx.Route:
    return mefi_mock.post(SEARCH_URL).respond(json=make_search_page(leads))


async def snapshot(
    engine: AsyncEngine, mefi_client: MefiClient, app_config: AppConfig, now: datetime
) -> int:
    outcome = await snapshot_with_clients(engine, mefi_client, app_config, now)
    return outcome.run_id


async def snapshot_with_clients(
    engine: AsyncEngine, mefi_client: MefiClient, app_config: AppConfig, now: datetime
) -> SnapshotOutcome:
    # Один httpx-клиент на оба ключа: respx различает запросы по пути, ключ тесту не важен.
    sources = SnapshotSources(
        leads_client=mefi_client, clients_client=mefi_client, contact_hash_key=TEST_CONTACT_HASH_KEY
    )
    return await run_daily_snapshot(engine, sources, app_config.status_mapping, "sofabelle", now)


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


async def test_snapshot_below_thresholds_is_success_with_alert_data(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    broken = make_lead(id=4000)
    del broken["created_at"]
    without_duplicate_flag = make_lead(id=4001, status=None)
    del without_duplicate_flag["is_duplicate"]
    mefi_returns(mefi_mock, [*recorded_search_leads(), without_duplicate_flag, broken])

    run_id = await snapshot(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    run = await run_row(engine, run_id)
    assert (run.status, run.attempt, run.snapshot_date) == ("success", 1, date(2026, 9, 24))
    assert (run.api_total, run.leads_written, run.unmapped_count, run.skipped_count) == (5, 4, 1, 1)
    assert run.is_duplicate_missing == 1
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


async def test_snapshot_writes_contact_keys_and_no_contacts(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    leads = recorded_search_leads()
    mefi_returns(mefi_mock, leads)

    await snapshot(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    async with engine.connect() as connection:
        rows = (
            await connection.execute(
                select(
                    lead_snapshots.c.lead_id,
                    lead_snapshots.c.raw,
                    lead_snapshots.c.contact_phone_key,
                    lead_snapshots.c.contact_email_key,
                ).order_by(lead_snapshots.c.lead_id)
            )
        ).all()
    stored_text = " ".join(
        f"{row.raw} {row.contact_phone_key} {row.contact_email_key}" for row in rows
    )
    for lead in leads:
        for contact in (lead["phone"], lead["email"], lead["name"]):
            if contact:
                assert contact not in stored_text
        assert lead["phone"].removeprefix("+40") not in stored_text
    assert all(row.contact_phone_key is not None for row in rows)
    leads_with_email = sorted(lead["id"] for lead in leads if lead["email"])
    assert [row.lead_id for row in rows if row.contact_email_key is not None] == leads_with_email
    assert len({row.contact_phone_key for row in rows}) == 3


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


@pytest.mark.parametrize(
    ("now", "expected_snapshot_date"),
    [
        (datetime(2026, 9, 24, 23, 59, tzinfo=BUCHAREST), date(2026, 9, 24)),
        (datetime(2026, 9, 25, 0, 0, tzinfo=BUCHAREST), date(2026, 9, 25)),
        (datetime(2026, 9, 24, 21, 30, tzinfo=UTC), date(2026, 9, 25)),
        # Последнее воскресенье октября: смещение Бухареста меняется с +3 на +2.
        (datetime(2026, 10, 24, 21, 0, tzinfo=UTC), date(2026, 10, 25)),
        (datetime(2026, 10, 25, 21, 0, tzinfo=UTC), date(2026, 10, 25)),
        (datetime(2026, 10, 25, 22, 0, tzinfo=UTC), date(2026, 10, 26)),
    ],
)
async def test_snapshot_date_is_bucharest_date(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
    now: datetime,
    expected_snapshot_date: date,
) -> None:
    mefi_returns(mefi_mock, [make_lead(id=1)])

    run_id = await snapshot(engine, mefi_client, app_config, now)

    assert (await run_row(engine, run_id)).snapshot_date == expected_snapshot_date


async def test_failed_run_error_and_log_carry_no_client_data(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
    caplog: pytest.LogCaptureFixture,
) -> None:
    page = make_search_page([make_lead(id=1)])
    page["data"] = "CLIENT_TEST +40700000077 client@example.test"
    mefi_mock.post(SEARCH_URL).respond(json=page)

    with pytest.raises(ValidationError):
        await snapshot(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    async with engine.connect() as connection:
        run = (await connection.execute(select(snapshot_runs))).one()
    assert run.error == "ValidationError: data: list_type"
    assert "+40700000077" not in caplog.text


async def failed_run(engine: AsyncEngine) -> Any:
    async with engine.connect() as connection:
        return (await connection.execute(select(snapshot_runs))).one()


async def test_mass_skip_fails_run_and_writes_no_rows(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    broken_leads = [make_lead(id=lead_id, created_at=None) for lead_id in range(100, 111)]
    mefi_returns(mefi_mock, [make_lead(id=1), *broken_leads])

    with pytest.raises(SnapshotIncomplete):
        await snapshot(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    run = await failed_run(engine)
    assert (run.status, run.error) == (
        "failed",
        "SnapshotIncomplete: пропущено 11 битых лидов из 12",
    )
    assert (run.api_total, run.leads_written, run.skipped_count) == (12, 1, 11)
    assert run.skipped_leads[0] == {"lead_id": 100, "reason": "created_at: datetime_type"}
    assert await snapshot_lead_ids(engine, date(2026, 9, 24)) == []


async def test_short_pagination_fails_run(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    leads = [make_lead(id=lead_id) for lead_id in range(1, 4)]
    mefi_mock.post(SEARCH_URL).respond(json=make_search_page(leads, total=20))

    with pytest.raises(SnapshotIncomplete):
        await snapshot(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    assert (await failed_run(engine)).error == "SnapshotIncomplete: получено 3 лидов из 20"


async def test_unsuccessful_page_fails_run(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    page = make_search_page([])
    page["success"] = False
    mefi_mock.post(SEARCH_URL).respond(json=page)

    with pytest.raises(MefiSearchUnsuccessful):
        await snapshot(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    assert (await failed_run(engine)).error == (
        "MefiSearchUnsuccessful: mefi вернул success: false на странице 1"
    )


def cancel_request(request: httpx.Request) -> httpx.Response:
    raise asyncio.CancelledError


async def test_cancelled_snapshot_marks_run_failed(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    mefi_mock.post(SEARCH_URL).mock(side_effect=cancel_request)

    with pytest.raises(asyncio.CancelledError):
        await snapshot(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    run = await failed_run(engine)
    assert (run.status, run.error) == ("failed", "CancelledError")


async def test_stale_running_run_is_superseded_by_next_attempt(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            insert(snapshot_runs).values(
                tenant_id="sofabelle", snapshot_date=date(2026, 9, 24), attempt=1, status="running"
            )
        )
    mefi_returns(mefi_mock, [make_lead(id=1)])

    run_id = await snapshot(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    async with engine.connect() as connection:
        runs = (
            await connection.execute(
                select(
                    snapshot_runs.c.attempt, snapshot_runs.c.status, snapshot_runs.c.error
                ).order_by(snapshot_runs.c.attempt)
            )
        ).all()
    assert [tuple(run) for run in runs] == [(1, "failed", "superseded"), (2, "success", None)]
    assert (await run_row(engine, run_id)).attempt == 2


async def test_lead_skipped_today_is_not_counted_as_missing(
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
            httpx.Response(
                200, json=make_search_page([make_lead(id=1), make_lead(id=2, created_at=None)])
            ),
        ]
    )

    await snapshot(engine, mefi_client, app_config, SEPTEMBER_23_EVENING)
    run_id = await snapshot(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    run = await run_row(engine, run_id)
    assert (run.skipped_count, run.missing_since_previous) == (1, 1)


def mefi_returns_clients(mefi_mock: respx.MockRouter, clients: list[dict[str, Any]]) -> respx.Route:
    return mefi_mock.post(CLIENTS_SEARCH_URL).respond(json=make_search_page(clients))


async def client_snapshot_rows(engine: AsyncEngine) -> list[Any]:
    async with engine.connect() as connection:
        return list(
            (
                await connection.execute(
                    select(client_snapshots).order_by(client_snapshots.c.client_id)
                )
            ).all()
        )


async def test_clients_snapshot_stores_rows_without_personal_data(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    clients = recorded_search_clients()
    mefi_returns(mefi_mock, recorded_search_leads())
    clients_route = mefi_returns_clients(mefi_mock, clients)

    outcome = await snapshot_with_clients(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    run = await run_row(engine, outcome.run_id)
    assert (run.status, run.clients_status, run.clients_error) == ("success", "success", None)
    assert (run.clients_api_total, run.clients_written, run.clients_skipped) == (3, 3, 0)
    assert outcome.clients_alert is None
    assert json.loads(clients_route.calls.last.request.content)["filters"] == {}
    rows = await client_snapshot_rows(engine)
    assert [(row.client_id, row.showroom, row.state) for row in rows] == [
        (2189, "Brașov", "lost"),
        (2280, "București", "active"),
        (2285, "Cluj", "active"),
    ]
    stored_text = " ".join(str(row.raw) for row in rows)
    for client in clients:
        assert client["name"] not in stored_text
        for personal_key in ("identity", "banking", "billing", "shipping", "business", "website"):
            assert f"'{personal_key}'" not in stored_text


async def test_clients_failure_keeps_leads_success(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    mefi_returns(mefi_mock, recorded_search_leads())
    mefi_mock.post(CLIENTS_SEARCH_URL).respond(status_code=500)

    outcome = await snapshot_with_clients(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    run = await run_row(engine, outcome.run_id)
    assert (run.status, run.leads_written) == ("success", 3)
    assert (run.clients_status, run.clients_error) == ("failed", "HTTPStatusError: HTTP 500")
    assert outcome.clients_alert is not None
    assert "Contract Cantitate в d1 будет «—»" in outcome.clients_alert
    assert await client_snapshot_rows(engine) == []


async def test_rerun_loads_only_missing_clients(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    leads_route = mefi_returns(mefi_mock, recorded_search_leads())
    clients_route = mefi_mock.post(CLIENTS_SEARCH_URL)
    clients_route.side_effect = [
        httpx.Response(500),
        httpx.Response(200, json=make_search_page(recorded_search_clients())),
    ]
    first = await snapshot_with_clients(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    second = await snapshot_with_clients(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)
    third = await snapshot_with_clients(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    assert first.run_id == second.run_id == third.run_id
    assert (leads_route.call_count, clients_route.call_count) == (1, 2)
    assert (second.clients_alert, third.clients_alert) == (None, None)
    run = await run_row(engine, first.run_id)
    assert (run.attempt, run.status, run.clients_status, run.clients_error) == (
        1,
        "success",
        "success",
        None,
    )
    assert len(await client_snapshot_rows(engine)) == 3


async def test_unknown_client_key_is_not_stored_and_alerted(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    mefi_returns(mefi_mock, recorded_search_leads())
    mefi_returns_clients(mefi_mock, [make_client(cnp="1900101000000")])

    outcome = await snapshot_with_clients(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    run = await run_row(engine, outcome.run_id)
    assert (run.clients_status, run.clients_unknown_keys) == ("success", ["cnp"])
    assert outcome.clients_alert is not None
    assert "незнакомые ключи cnp" in outcome.clients_alert
    assert "1900101000000" not in outcome.clients_alert
    (row,) = await client_snapshot_rows(engine)
    assert "cnp" not in row.raw
