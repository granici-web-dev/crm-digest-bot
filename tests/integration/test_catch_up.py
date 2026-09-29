import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine, Iterator
from datetime import date, datetime, time, timedelta
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import NullPool

from digest.app import (
    catch_up_on_startup,
    missed_snapshot_job,
    report_missed_snapshots,
    seed_defaults,
    stored_schedules,
    take_late_snapshot,
)
from digest.config import AppConfig
from digest.db.engine import create_database_engine
from digest.db.schema import lead_snapshots, module_settings, report_runs, snapshot_runs
from digest.delivery.ops import OpsChannel
from digest.mefi.client import MefiClient, RequestPacer, create_mefi_http_client
from digest.reports.modules import IMPLEMENTED_MODULES
from digest.reports.runner import ReportDeps, run_report
from digest.snapshot import SnapshotSources, SnapshotTrigger
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
LATE_SNAPSHOT_LINE = "Date extrase din mefi la "
SUNDAY = date(2026, 9, 27)
MONDAY = date(2026, 9, 28)
MONDAY_MORNING = datetime(2026, 9, 28, 10, 0, tzinfo=BUCHAREST)
MONDAY_EVENING = datetime(2026, 9, 28, 21, 47, tzinfo=BUCHAREST)

MefiHandler = Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]


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

    def report_texts(self, title: str) -> list[str]:
        return [message.text for message in self.group.sent if title in message.text]


async def no_wait(seconds: float) -> None:
    return None


def paced_sources(http_client: httpx.AsyncClient) -> SnapshotSources:
    client = MefiClient(http_client, pacer=RequestPacer(1.2, sleep=no_wait))
    return SnapshotSources(
        leads_client=client, clients_client=client, contact_hash_key=TEST_CONTACT_HASH_KEY
    )


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
        yield paced_sources(http_client)


def mefi_returns_recorded_data(mefi_mock: respx.MockRouter) -> respx.Route:
    mefi_mock.post(f"{BASE_URL}/clients/search").respond(
        json=make_search_page(recorded_search_clients())
    )
    return mefi_mock.post(f"{BASE_URL}/leads/search").respond(
        json=make_search_page(recorded_search_leads())
    )


async def store_success_snapshot(
    engine: AsyncEngine,
    snapshot_date: date,
    trigger: SnapshotTrigger = "scheduled",
    started_at: datetime | None = None,
) -> None:
    row = make_snapshot_row(
        lead_id=1, created_at=datetime.combine(snapshot_date, time(11, 0), tzinfo=BUCHAREST)
    )
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
                trigger=trigger,
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


async def start_like_run_app(harness: Harness, sources: SnapshotSources, now: datetime) -> None:
    # Порядок run_app: поздний снапшот до планировщика, затем пропуски и отчёты.
    await take_late_snapshot(harness.deps, sources, now)
    schedules_by_level = await stored_schedules(harness.deps.engine, TENANT_ID)
    await catch_up_on_startup(harness.deps, now, schedules_by_level)


async def test_late_snapshot_is_not_taken_when_today_success_exists(
    harness: Harness, snapshot_sources: SnapshotSources, mefi_mock: respx.MockRouter
) -> None:
    await store_success_snapshot(harness.deps.engine, MONDAY)
    leads_route = mefi_returns_recorded_data(mefi_mock)

    await take_late_snapshot(harness.deps, snapshot_sources, MONDAY_EVENING)

    assert leads_route.call_count == 0
    assert [row["status"] for row in await snapshot_run_rows(harness.deps.engine)] == ["success"]
    assert harness.ops_texts == []


async def test_late_snapshot_is_taken_after_retry_time_and_announced(
    harness: Harness, snapshot_sources: SnapshotSources, mefi_mock: respx.MockRouter
) -> None:
    mefi_returns_recorded_data(mefi_mock)

    await take_late_snapshot(harness.deps, snapshot_sources, MONDAY_EVENING)

    [run] = await snapshot_run_rows(harness.deps.engine)
    assert (run["snapshot_date"], run["status"], run["trigger"]) == (MONDAY, "success", "catch_up")
    assert run["started_at"] == MONDAY_EVENING
    assert harness.ops_texts == ["Снапшот за 28.09.2026 снят с опозданием в 21:47."]


async def test_late_snapshot_without_clients_says_clients_not_taken(
    harness: Harness, snapshot_sources: SnapshotSources, mefi_mock: respx.MockRouter
) -> None:
    mefi_mock.post(f"{BASE_URL}/leads/search").respond(
        json=make_search_page(recorded_search_leads())
    )
    mefi_mock.post(f"{BASE_URL}/clients/search").respond(status_code=500)

    await take_late_snapshot(harness.deps, snapshot_sources, MONDAY_EVENING)

    clients_alert, late_message = harness.ops_texts
    assert clients_alert.startswith("Снапшот клиентов mefi за 28.09.2026 не удался")
    assert late_message == (
        "Снапшот лидов за 28.09.2026 снят с опозданием в 21:47, клиенты не сняты: "
        "Contract Cantitate в d1 будет «—»."
    )


async def test_revoked_leads_key_alert_says_what_to_do(
    harness: Harness, snapshot_sources: SnapshotSources, mefi_mock: respx.MockRouter
) -> None:
    mefi_mock.post(f"{BASE_URL}/leads/search").respond(
        401, json={"success": False, "message": "Autentificare eșuată"}
    )

    await take_late_snapshot(harness.deps, snapshot_sources, MONDAY_EVENING)

    assert harness.ops_texts == [
        "Снапшот за 28.09.2026 21:47 не удался: mefi отклонил ключ leads:read "
        "(Autentificare eșuată): создайте новый ключ на странице API mefi и замените "
        "MEFI_API_KEY в .env, затем перезапустите бота."
    ]
    [run] = await snapshot_run_rows(harness.deps.engine)
    assert run["status"] == "failed"


async def test_late_snapshot_leaves_fresh_running_row_alone(
    harness: Harness, snapshot_sources: SnapshotSources, mefi_mock: respx.MockRouter
) -> None:
    leads_route = mefi_returns_recorded_data(mefi_mock)
    async with harness.deps.engine.begin() as connection:
        await connection.execute(
            insert(snapshot_runs).values(
                tenant_id=TENANT_ID,
                snapshot_date=MONDAY,
                attempt=1,
                status="running",
                trigger="catch_up",
                started_at=MONDAY_EVENING - timedelta(minutes=2),
            )
        )

    await take_late_snapshot(harness.deps, snapshot_sources, MONDAY_EVENING)

    assert leads_route.call_count == 0
    assert [row["status"] for row in await snapshot_run_rows(harness.deps.engine)] == ["running"]
    assert harness.ops_texts == []


async def test_startup_after_midnight_closes_yesterday_as_missed_without_snapshot(
    harness: Harness, snapshot_sources: SnapshotSources, mefi_mock: respx.MockRouter
) -> None:
    await store_success_snapshot(harness.deps.engine, date(2026, 9, 25))
    leads_route = mefi_returns_recorded_data(mefi_mock)

    await start_like_run_app(
        harness, snapshot_sources, datetime(2026, 9, 27, 0, 30, tzinfo=BUCHAREST)
    )

    assert leads_route.call_count == 0
    rows = await snapshot_run_rows(harness.deps.engine)
    assert [
        (row["snapshot_date"], row["status"], row["attempt"], row["trigger"]) for row in rows
    ] == [
        (date(2026, 9, 25), "success", 1, "scheduled"),
        (date(2026, 9, 26), "missed", None, "catch_up"),
    ]
    assert harness.ops_texts == ["Снапшот за 26.09.2026 пропущен, данные дня не восстановить."]
    assert harness.group.sent == []


async def test_missed_snapshot_check_is_silent_when_yesterday_snapshot_exists(
    harness: Harness,
) -> None:
    await store_success_snapshot(harness.deps.engine, SUNDAY)

    await report_missed_snapshots(harness.deps, MONDAY, "scheduled")

    assert harness.ops_texts == []
    assert len(await snapshot_run_rows(harness.deps.engine)) == 1


async def test_missed_snapshot_is_reported_once(harness: Harness) -> None:
    await store_success_snapshot(harness.deps.engine, date(2026, 9, 25))

    await report_missed_snapshots(harness.deps, MONDAY, "catch_up")
    await report_missed_snapshots(harness.deps, MONDAY, "scheduled")

    assert harness.ops_texts == [
        "Снапшоты за 26.09.2026, 27.09.2026 пропущены, данные этих дней не восстановить."
    ]
    rows = await snapshot_run_rows(harness.deps.engine)
    missed = [row["snapshot_date"] for row in rows if row["status"] == "missed"]
    assert missed == [date(2026, 9, 26), SUNDAY]


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

    await start_like_run_app(harness, snapshot_sources, MONDAY_MORNING)

    assert len(harness.group.sent) == sent_before
    assert len(harness.ops_texts) == ops_before
    assert len(await report_run_rows(harness.deps.engine)) == 1


async def test_catch_up_sends_missed_weekly_with_late_line(
    harness: Harness, snapshot_sources: SnapshotSources
) -> None:
    await store_success_snapshot(harness.deps.engine, SUNDAY)

    await start_like_run_app(harness, snapshot_sources, MONDAY_MORNING)

    assert harness.group.sent[0].text.splitlines()[0] == LATE_LINE
    assert "Raport săptămânal" in harness.group_text
    [run] = await report_run_rows(harness.deps.engine)
    assert (run["report_level"], run["status"]) == ("weekly", "success")


async def test_catch_up_skips_weekly_after_catch_up_days(
    harness: Harness, snapshot_sources: SnapshotSources
) -> None:
    await store_success_snapshot(harness.deps.engine, date(2026, 9, 20))

    await start_like_run_app(
        harness, snapshot_sources, datetime(2026, 9, 24, 10, 0, tzinfo=BUCHAREST)
    )

    assert harness.group.sent == []
    assert await report_run_rows(harness.deps.engine) == []


async def test_catch_up_skips_yearly(harness: Harness, snapshot_sources: SnapshotSources) -> None:
    await store_success_snapshot(harness.deps.engine, date(2027, 1, 5))

    await start_like_run_app(
        harness, snapshot_sources, datetime(2027, 1, 6, 10, 0, tzinfo=BUCHAREST)
    )

    # Среда: недельный отчёт в пределах catch_up_days догоняется, годовой нет.
    assert harness.report_texts("Raport anual") == []
    levels = [run["report_level"] for run in await report_run_rows(harness.deps.engine)]
    assert "yearly" not in levels
    assert not any("yearly" in alert for alert in harness.ops_texts)


async def test_catch_up_skips_level_with_every_module_switched_off(
    harness: Harness, snapshot_sources: SnapshotSources
) -> None:
    await store_success_snapshot(harness.deps.engine, SUNDAY)
    async with harness.deps.engine.begin() as connection:
        await connection.execute(
            insert(module_settings),
            [
                {"tenant_id": TENANT_ID, "module_id": module_id, "enabled": False}
                for module_id in harness.deps.config.modules.weekly
            ],
        )

    await start_like_run_app(harness, snapshot_sources, MONDAY_MORNING)

    assert harness.group.sent == []
    assert harness.ops_texts == []
    assert await report_run_rows(harness.deps.engine) == []


async def test_daily_catch_up_needs_today_success_snapshot(
    harness: Harness, snapshot_sources: SnapshotSources, mefi_mock: respx.MockRouter
) -> None:
    await store_success_snapshot(harness.deps.engine, SUNDAY)
    mefi_mock.post(f"{BASE_URL}/leads/search").respond(status_code=500)

    await start_like_run_app(harness, snapshot_sources, MONDAY_EVENING)

    daily_runs = [
        run for run in await report_run_rows(harness.deps.engine) if run["report_level"] == "daily"
    ]
    assert daily_runs == []
    assert harness.report_texts("Raport zilnic") == []
    assert any("Снапшот за 28.09.2026 21:47 не удался" in alert for alert in harness.ops_texts)


async def test_daily_catch_up_after_late_snapshot_marks_both(
    harness: Harness, snapshot_sources: SnapshotSources, mefi_mock: respx.MockRouter
) -> None:
    await store_success_snapshot(harness.deps.engine, SUNDAY)
    mefi_returns_recorded_data(mefi_mock)

    await start_like_run_app(harness, snapshot_sources, MONDAY_EVENING)

    [daily_text] = harness.report_texts("Raport zilnic")
    lines = daily_text.splitlines()
    assert lines[0] == LATE_LINE
    assert (
        "Date extrase din mefi la 21:47, nu la 19:00; schimbările de status dintre 19:00 și "
        "21:47 sunt incluse în ziua de azi." in lines
    )


@pytest.mark.parametrize(
    "started_at",
    [
        datetime(2026, 9, 28, 19, 10, 0, 300000, tzinfo=BUCHAREST),
        datetime(2026, 9, 28, 19, 12, tzinfo=BUCHAREST),
    ],
)
async def test_scheduled_retry_snapshot_gives_no_late_snapshot_line(
    harness: Harness, started_at: datetime
) -> None:
    await store_success_snapshot(harness.deps.engine, MONDAY, "retry", started_at)

    await run_report(
        "daily", datetime(2026, 9, 28, 19, 30, tzinfo=BUCHAREST), harness.deps, late=False
    )

    assert LATE_SNAPSHOT_LINE not in harness.group_text
    assert LATE_LINE not in harness.group_text


@pytest.mark.parametrize(
    ("trigger", "started_at", "shown_time"),
    [
        ("catch_up", datetime(2026, 9, 28, 21, 47, tzinfo=BUCHAREST), "21:47"),
        ("manual", datetime(2026, 9, 28, 20, 5, tzinfo=BUCHAREST), "20:05"),
    ],
)
async def test_unscheduled_snapshot_gives_late_snapshot_line(
    harness: Harness, trigger: SnapshotTrigger, started_at: datetime, shown_time: str
) -> None:
    await store_success_snapshot(harness.deps.engine, MONDAY, trigger, started_at)

    await run_report("daily", started_at, harness.deps, late=False)

    assert f"{LATE_SNAPSHOT_LINE}{shown_time}, nu la 19:00" in harness.group_text


async def test_weekly_report_ignores_late_sunday_snapshot(harness: Harness) -> None:
    await store_success_snapshot(
        harness.deps.engine, SUNDAY, "catch_up", datetime(2026, 9, 27, 22, 0, tzinfo=BUCHAREST)
    )

    await run_report(
        "weekly", datetime(2026, 9, 28, 9, 0, tzinfo=BUCHAREST), harness.deps, late=False
    )

    assert LATE_SNAPSHOT_LINE not in harness.group_text


def recorded_mefi_held_until(requested: asyncio.Event, release: asyncio.Event) -> MefiHandler:
    leads_page = make_search_page(recorded_search_leads())
    clients_page = make_search_page(recorded_search_clients())

    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/leads/search"):
            requested.set()
            await release.wait()
            return httpx.Response(200, json=leads_page)
        return httpx.Response(200, json=clients_page)

    return handle


async def test_two_processes_starting_late_take_one_snapshot_and_send_once(
    engine: AsyncEngine, database_url: str, app_config: AppConfig
) -> None:
    await seed_defaults(engine, app_config, TENANT_ID)
    await store_success_snapshot(engine, SUNDAY)
    second_engine = create_database_engine(database_url, poolclass=NullPool)
    first, second = Harness(engine, app_config), Harness(second_engine, app_config)
    first_requested_leads, release_first = asyncio.Event(), asyncio.Event()
    second_mefi_calls: list[str] = []

    async def second_mefi(request: httpx.Request) -> httpx.Response:
        second_mefi_calls.append(request.url.path)
        return httpx.Response(500)

    try:
        async with (
            httpx.AsyncClient(
                base_url=BASE_URL,
                transport=httpx.MockTransport(
                    recorded_mefi_held_until(first_requested_leads, release_first)
                ),
            ) as first_http,
            httpx.AsyncClient(
                base_url=BASE_URL, transport=httpx.MockTransport(second_mefi)
            ) as second_http,
        ):
            first_start = asyncio.create_task(
                start_like_run_app(first, paced_sources(first_http), MONDAY_EVENING)
            )
            # Первый процесс прошёл блокировку даты, записал running и ждёт ответа mefi.
            await first_requested_leads.wait()
            await start_like_run_app(second, paced_sources(second_http), MONDAY_EVENING)
            release_first.set()
            await first_start
    finally:
        await second_engine.dispose()

    assert second_mefi_calls == []
    rows = await snapshot_run_rows(engine)
    assert [(row["snapshot_date"], row["status"]) for row in rows] == [
        (SUNDAY, "success"),
        (MONDAY, "success"),
    ]
    ops_texts = first.ops_texts + second.ops_texts
    assert ops_texts.count("Снапшот за 28.09.2026 снят с опозданием в 21:47.") == 1
    assert not any("не удался" in text for text in ops_texts)
    daily = first.report_texts("Raport zilnic") + second.report_texts("Raport zilnic")
    weekly = first.report_texts("Raport săptămânal") + second.report_texts("Raport săptămânal")
    assert (len(daily), len(weekly)) == (1, 1)
    levels = sorted(run["report_level"] for run in await report_run_rows(engine))
    assert levels == ["daily", "weekly"]
