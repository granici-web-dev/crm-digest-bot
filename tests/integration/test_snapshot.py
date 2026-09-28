import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from pydantic import SecretStr, ValidationError
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.app import missing_final_snapshot_alerts
from digest.config import AppConfig
from digest.db.lead_frame import SnapshotMissingError, load_lead_frame
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
    SnapshotRunSuperseded,
    SnapshotSources,
    run_daily_snapshot,
)
from factories import (
    TEST_CONTACT_HASH_KEY,
    FakeClientsSearch,
    make_client,
    make_custom_fields,
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
    assert json.loads(clients_route.calls[0].request.content)["filters"] == {}
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
        httpx.Response(200, json=make_search_page(recorded_search_clients())),
    ]
    first = await snapshot_with_clients(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    second = await snapshot_with_clients(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)
    third = await snapshot_with_clients(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    assert first.run_id == second.run_id == third.run_id
    assert (leads_route.call_count, clients_route.call_count) == (1, 3)
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


async def test_clients_short_of_total_below_threshold_are_success_with_alert(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    mefi_returns(mefi_mock, recorded_search_leads())
    mefi_mock.post(CLIENTS_SEARCH_URL).respond(
        json=make_search_page([make_client(id=10), make_client(id=11)], total=3)
    )

    outcome = await snapshot_with_clients(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    run = await run_row(engine, outcome.run_id)
    assert (run.clients_status, run.clients_api_total, run.clients_written) == ("success", 3, 2)
    assert outcome.clients_alert == (
        "Выгрузка клиентов mefi за 24.09.2026 неполная: получено 2 из 3; "
        "22.09.2026–25.09.2026: получено 2 из 3. Contract Cantitate может быть занижен."
    )


SEPTEMBER_24_NOON = datetime(2026, 9, 24, 12, 8, tzinfo=BUCHAREST)


async def test_snapshot_before_window_end_is_preview_invisible_to_reports(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    mefi_returns(mefi_mock, [make_lead(id=1), make_lead(id=2)])
    mefi_returns_clients(mefi_mock, [make_client(id=10)])

    outcome = await snapshot_with_clients(engine, mefi_client, app_config, SEPTEMBER_24_NOON)

    run = await run_row(engine, outcome.run_id)
    assert (outcome.status, run.status, run.clients_status) == ("preview", "preview", "success")
    assert await snapshot_lead_ids(engine, date(2026, 9, 24)) == [1, 2]
    with pytest.raises(SnapshotMissingError):
        await load_lead_frame(engine, "sofabelle", date(2026, 9, 24), app_config)


async def test_day_snapshot_replaces_preview_of_same_date(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    mefi_mock.post(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=make_search_page([make_lead(id=1), make_lead(id=2)])),
            httpx.Response(200, json=make_search_page([make_lead(id=1), make_lead(id=3)])),
        ]
    )
    fake_clients = FakeClientsSearch([make_client(id=10)])
    mefi_mock.post(CLIENTS_SEARCH_URL).mock(side_effect=fake_clients)
    preview = await snapshot_with_clients(engine, mefi_client, app_config, SEPTEMBER_24_NOON)
    fake_clients.clients = [make_client(id=11)]

    day = await snapshot_with_clients(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    assert (await run_row(engine, preview.run_id)).status == "superseded"
    day_run = await run_row(engine, day.run_id)
    assert (day.status, day_run.status, day_run.attempt, day_run.clients_status) == (
        "success",
        "success",
        2,
        "success",
    )
    assert await snapshot_lead_ids(engine, date(2026, 9, 24)) == [1, 3]
    assert [row.client_id for row in await client_snapshot_rows(engine)] == [11]
    frame = await load_lead_frame(engine, "sofabelle", date(2026, 9, 24), app_config)
    assert sorted(frame["lead_id"]) == [1, 3]


async def test_failed_day_snapshot_keeps_preview_untouched(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
) -> None:
    mefi_mock.post(SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=make_search_page([make_lead(id=1), make_lead(id=2)])),
            httpx.Response(200, json=make_search_page([make_lead(id=1)], total=20)),
        ]
    )
    mefi_returns_clients(mefi_mock, [make_client(id=10)])
    preview = await snapshot_with_clients(engine, mefi_client, app_config, SEPTEMBER_24_NOON)

    with pytest.raises(SnapshotIncomplete):
        await snapshot_with_clients(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    assert (await run_row(engine, preview.run_id)).status == "preview"
    assert await snapshot_lead_ids(engine, date(2026, 9, 24)) == [1, 2]
    assert [row.client_id for row in await client_snapshot_rows(engine)] == [10]


SEPTEMBER_24_BEFORE_WINDOW_END = datetime(2026, 9, 24, 18, 59, tzinfo=BUCHAREST)
MefiHandler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


def paced_mefi_client(http_client: httpx.AsyncClient) -> MefiClient:
    return MefiClient(http_client, pacer=RequestPacer(1.2, sleep=no_wait))


def mock_mefi(leads: list[dict[str, Any]], clients: list[dict[str, Any]]) -> MefiHandler:
    clients_search = FakeClientsSearch(clients)

    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/leads/search"):
            return httpx.Response(200, json=make_search_page(leads))
        return clients_search(request)

    return handle


def held_until(
    handler: MefiHandler, path_suffix: str, requested: asyncio.Event, release: asyncio.Event
) -> MefiHandler:
    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(path_suffix):
            requested.set()
            await release.wait()
        return await handler(request)

    return handle


async def run_preview_overtaken_by_day_snapshot(
    engine: AsyncEngine, app_config: AppConfig, held_path_suffix: str
) -> tuple[asyncio.Task[SnapshotOutcome], SnapshotOutcome]:
    preview_requested, day_done = asyncio.Event(), asyncio.Event()
    preview_mefi = held_until(
        mock_mefi([make_lead(id=1), make_lead(id=2)], [make_client(id=10)]),
        held_path_suffix,
        preview_requested,
        day_done,
    )
    day_mefi = mock_mefi([make_lead(id=1), make_lead(id=3)], [make_client(id=11)])
    async with (
        httpx.AsyncClient(
            base_url=BASE_URL, transport=httpx.MockTransport(preview_mefi)
        ) as preview_http,
        httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(day_mefi)) as day_http,
    ):
        preview_task = asyncio.create_task(
            snapshot_with_clients(
                engine, paced_mefi_client(preview_http), app_config, SEPTEMBER_24_BEFORE_WINDOW_END
            )
        )
        await preview_requested.wait()
        day = await snapshot_with_clients(
            engine, paced_mefi_client(day_http), app_config, SEPTEMBER_24_EVENING
        )
        day_done.set()
        await asyncio.wait([preview_task])
    return preview_task, day


async def assert_day_snapshot_intact(
    engine: AsyncEngine, app_config: AppConfig, day: SnapshotOutcome
) -> None:
    day_run = await run_row(engine, day.run_id)
    assert (day_run.status, day_run.clients_status) == ("success", "success")
    assert await snapshot_lead_ids(engine, date(2026, 9, 24)) == [1, 3]
    assert [row.client_id for row in await client_snapshot_rows(engine)] == [11]
    frame = await load_lead_frame(engine, "sofabelle", date(2026, 9, 24), app_config)
    assert sorted(frame["lead_id"]) == [1, 3]


async def test_preview_leads_finishing_after_day_snapshot_do_not_overwrite_it(
    engine: AsyncEngine, app_config: AppConfig
) -> None:
    preview_task, day = await run_preview_overtaken_by_day_snapshot(
        engine, app_config, "/leads/search"
    )

    with pytest.raises(SnapshotRunSuperseded):
        preview_task.result()
    await assert_day_snapshot_intact(engine, app_config, day)
    async with engine.connect() as connection:
        preview_run = (
            await connection.execute(select(snapshot_runs).where(snapshot_runs.c.attempt == 1))
        ).one()
    assert (preview_run.status, preview_run.error, preview_run.leads_written) == (
        "failed",
        "superseded",
        None,
    )


async def test_preview_clients_finishing_after_day_snapshot_are_not_written(
    engine: AsyncEngine, app_config: AppConfig
) -> None:
    preview_task, day = await run_preview_overtaken_by_day_snapshot(
        engine, app_config, "/clients/search"
    )

    preview = preview_task.result()
    assert preview.clients_alert is None
    preview_run = await run_row(engine, preview.run_id)
    assert (preview_run.status, preview_run.clients_status) == ("superseded", None)
    await assert_day_snapshot_intact(engine, app_config, day)


async def store_runs(engine: AsyncEngine, runs: list[tuple[date, str]]) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            insert(snapshot_runs),
            [
                {
                    "tenant_id": "sofabelle",
                    "snapshot_date": run_date,
                    "attempt": 1,
                    "status": status,
                }
                for run_date, status in runs
            ],
        )


async def test_missing_final_snapshot_of_today_is_alerted_after_window_end(
    engine: AsyncEngine,
) -> None:
    await store_runs(engine, [(date(2026, 9, 23), "success"), (date(2026, 9, 24), "failed")])

    alerts = await missing_final_snapshot_alerts(
        engine, "sofabelle", datetime(2026, 9, 24, 19, 40, tzinfo=BUCHAREST), time(19, 0)
    )

    assert alerts == [
        "Нет финального снапшота mefi за 24.09.2026: отчёты за этот день не построятся. "
        "До полуночи его можно снять вручную: python -m digest snapshot."
    ]


async def test_before_window_end_only_yesterday_is_checked(engine: AsyncEngine) -> None:
    await store_runs(engine, [(date(2026, 9, 23), "preview")])

    alerts = await missing_final_snapshot_alerts(
        engine, "sofabelle", datetime(2026, 9, 24, 9, 0, tzinfo=BUCHAREST), time(19, 0)
    )

    assert alerts == [
        "Нет финального снапшота mefi за 23.09.2026: отчёты за этот день не построятся, "
        "задним числом его не снять."
    ]


async def test_final_snapshots_of_today_and_yesterday_give_no_alert(engine: AsyncEngine) -> None:
    await store_runs(engine, [(date(2026, 9, 23), "success"), (date(2026, 9, 24), "success")])

    alerts = await missing_final_snapshot_alerts(
        engine, "sofabelle", datetime(2026, 9, 24, 19, 40, tzinfo=BUCHAREST), time(19, 0)
    )

    assert alerts == []


SYNTHETIC_PERSONAL_DATA = (
    "SINTETIC-NUME",
    "711000001",
    "sintetic.lead@example.test",
    "1900101009991",
    "711000002",
    "RO99999991",
    "Strada Sintetica 9",
    "711000003",
    "711000004",
    "711000005",
    "SINTETIC-CLIENT",
    "ZZ999991",
    "2900101009992",
    "RO49SINT0000000000009993",
    "Strada Facturare 7",
    "sintetic.example.test",
    "711000006",
    "711000007",
    "711000008",
    "711000009",
    "1900101009994",
)


def lead_with_synthetic_personal_data() -> dict[str, Any]:
    textarea = "Revine, sunati pe +40 711 000 004"
    return make_lead(
        id=1,
        name="Ion SINTETIC-NUME",
        phone="+40 711 000 001",
        email="sintetic.lead@example.test",
        identity={"card_number": None, "personal_id": "1900101009991"},
        business={"name": "SRL", "phone": "+40711000002"},
        company={"name": "SRL", "fiscal_code": "RO99999991"},
        location={"address_line": "Strada Sintetica 9", "city": "Cluj"},
        description="Sunati +40711000003",
        custom_fields=[
            *make_custom_fields(),
            {
                "field_id": 8,
                "name": "Revenire 1 (Data+Info)",
                "type": "textarea",
                "value": textarea,
            },
            {"field_id": 51, "name": "Mesaj", "type": "textarea", "value": textarea},
            {"field_id": 60, "name": "Telefon 2", "type": "input", "value": "+40711000005"},
        ],
    )


def client_with_synthetic_personal_data() -> dict[str, Any]:
    return make_client(
        id=10,
        name="Maria SINTETIC-CLIENT",
        identity={"card_number": "ZZ999991", "personal_id": "2900101009992"},
        banking={"bank_name": "BANCA", "iban": "RO49SINT0000000000009993"},
        billing={"address": "Strada Facturare 7", "city": "Cluj"},
        shipping={"address": "Strada Facturare 7", "city": "Cluj"},
        website="https://sintetic.example.test",
        custom_fields=[
            {"field_id": 15, "name": "Showroom", "type": "select", "value": "Cluj"},
            {"field_id": 13, "name": "Informatii", "type": "textarea", "value": "+40711000006"},
            {"field_id": 60, "name": "Telefon 2", "type": "input", "value": "+40711000007"},
        ],
        responsibles=[{"id": 12, "name": "Dragoi Mihaela", "phone": "+40711000008"}],
        elimination={
            "type": "lost",
            "reason": {"id": 3, "name": "BUGET"},
            "detailed_reason": "Clientul a sunat de pe +40711000009",
        },
        cnp="1900101009994",
    )


async def stored_text_of(engine: AsyncEngine, table_name: str) -> str:
    async with engine.connect() as connection:
        return str(
            await connection.scalar(
                text(f"SELECT coalesce(string_agg(stored::text, ' '), '') FROM {table_name} stored")
            )
        )


async def test_synthetic_personal_data_reaches_no_table_log_or_alert(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    mefi_mock: respx.MockRouter,
    app_config: AppConfig,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    mefi_returns(mefi_mock, [lead_with_synthetic_personal_data()])
    mefi_returns_clients(mefi_mock, [client_with_synthetic_personal_data()])

    outcome = await snapshot_with_clients(engine, mefi_client, app_config, SEPTEMBER_24_EVENING)

    run = await run_row(engine, outcome.run_id)
    assert (run.status, run.clients_status, run.leads_written, run.clients_written) == (
        "success",
        "success",
        1,
        1,
    )
    stored = " ".join(
        [
            await stored_text_of(engine, "lead_snapshots"),
            await stored_text_of(engine, "client_snapshots"),
            await stored_text_of(engine, "snapshot_runs"),
            outcome.clients_alert or "",
            caplog.text,
        ]
    )
    leaked = [value for value in SYNTHETIC_PERSONAL_DATA if value in stored]
    assert leaked == []
    assert outcome.clients_alert is not None
    assert "custom_fields.60 «Telefon 2»" in outcome.clients_alert
