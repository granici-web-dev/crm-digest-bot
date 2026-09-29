import asyncio
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine

from digest.app import (
    StoredSchedule,
    default_schedules,
    mefi_keys_line,
    nonstandard_schedule_alert,
    schedule_report_job,
    schedule_snapshot_jobs,
    startup_announcement,
)
from digest.config import AppConfig
from digest.delivery.ops import OpsChannel
from digest.mefi.client import MefiClient, RequestPacer, create_mefi_http_client
from digest.reports.modules import IMPLEMENTED_MODULES
from digest.reports.periods import ReportLevel
from digest.reports.runner import ReportDeps
from digest.snapshot import SnapshotSources
from factories import (
    TEST_CONTACT_HASH_KEY,
    FakeTime,
    make_client,
    make_lead_links,
    make_search_page,
)
from fakes import recording_bot


def test_startup_announcement_contains_keys_line() -> None:
    keys = "Ключи mefi: leads ✓, clients ✓."

    assert startup_announcement("abc1234", dry_run=True, mefi_keys=keys) == (
        "Бот запущен, версия abc1234, DRY_RUN=1.\nКлючи mefi: leads ✓, clients ✓."
    )
    assert "DRY_RUN=0." in startup_announcement("abc1234", dry_run=False, mefi_keys=keys)


MEFI_BASE_URL = "https://mefi.test/api/v1"


def fast_snapshot_sources(http_client: httpx.AsyncClient) -> SnapshotSources:
    fake_time = FakeTime()
    pacer = RequestPacer(1.2, sleep=fake_time.sleep, clock=fake_time.clock)
    return SnapshotSources(
        MefiClient(http_client, pacer), MefiClient(http_client, pacer), TEST_CONTACT_HASH_KEY
    )


@respx.mock
async def test_startup_line_marks_rejected_key_and_keeps_other() -> None:
    respx.post(f"{MEFI_BASE_URL}/leads/search").respond(
        401, json={"success": False, "message": "Autentificare eșuată"}
    )
    respx.post(f"{MEFI_BASE_URL}/clients/search").respond(
        json=make_search_page([make_client()], total=1)
    )

    async with create_mefi_http_client(MEFI_BASE_URL, SecretStr("test-key")) as http_client:
        line = await mefi_keys_line(fast_snapshot_sources(http_client))

    first_line, rejection = line.split("\n")
    assert first_line == "⚠️ Ключи mefi: leads ✗, clients ✓."
    assert rejection.startswith("mefi отклонил ключ leads:read (Autentificare eșuată)")
    assert "MEFI_API_KEY" in rejection


@respx.mock
async def test_startup_line_reports_unchecked_key_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def hang(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(10)
        raise AssertionError("unreachable")

    monkeypatch.setattr("digest.app.MEFI_KEY_CHECK_TIMEOUT_SECONDS", 0.05)
    respx.post(f"{MEFI_BASE_URL}/leads/search").mock(side_effect=hang)
    respx.post(f"{MEFI_BASE_URL}/clients/search").mock(side_effect=hang)

    async with create_mefi_http_client(MEFI_BASE_URL, SecretStr("test-key")) as http_client:
        async with asyncio.timeout(1):
            line = await mefi_keys_line(fast_snapshot_sources(http_client))

    assert line == (
        "Ключи mefi: leads не проверен (TimeoutError), clients не проверен (TimeoutError)."
    )


async def test_report_and_snapshot_jobs_tolerate_late_start(app_config: AppConfig) -> None:
    report_bot, _ = recording_bot()
    ops_bot, _ = recording_bot()
    deps = ReportDeps(
        engine=create_async_engine("postgresql+asyncpg://unused/unused"),
        config=app_config,
        tenant_id="sofabelle",
        report_bot=report_bot,
        ops=OpsChannel(ops_bot, -1003),
        report_chat_id=-1001,
        modules=IMPLEMENTED_MODULES,
        lead_links=make_lead_links(app_config.status_mapping),
    )
    scheduler = AsyncIOScheduler(timezone=ZoneInfo("Europe/Bucharest"))
    async with httpx.AsyncClient() as http_client:
        schedule_snapshot_jobs(
            scheduler,
            deps,
            SnapshotSources(
                MefiClient(http_client), MefiClient(http_client), TEST_CONTACT_HASH_KEY
            ),
        )
    schedule_report_job(scheduler, deps, None, "daily", "30 19 * * *")

    grace_by_job = {job.id: job.misfire_grace_time for job in scheduler.get_jobs()}

    assert grace_by_job == {
        "snapshot_1900": 300,
        "snapshot_1910": 300,
        "snapshot_check_1940": 1800,
        "missed_snapshot_check_0900": 1800,
        "report_daily": 1800,
    }


def test_default_schedules_take_time_from_send_times(app_config: AppConfig) -> None:
    assert default_schedules(app_config) == {
        "daily": "30 19 * * *",
        "weekly": "0 9 * * mon",
        "monthly": "0 9 1 * *",
        "yearly": "0 9 5 1 *",
    }


def test_nonstandard_schedule_alert_names_levels_outside_send_times(
    app_config: AppConfig,
) -> None:
    schedules_by_level: dict[ReportLevel, StoredSchedule] = {
        "daily": StoredSchedule("*/5 19 * * *", enabled=True),
        "weekly": StoredSchedule("0 9 * * mon", enabled=True),
        "monthly": StoredSchedule("15 7 1 * *", enabled=False),
        "yearly": StoredSchedule("0 9 5 1 *", enabled=True),
    }

    alert = nonstandard_schedule_alert(app_config, schedules_by_level)

    assert alert is not None
    assert alert.startswith(
        "Расписание вне вариантов send_times: daily «*/5 19 * * *», monthly «15 7 1 * *»."
    )


def test_standard_schedules_give_no_alert(app_config: AppConfig) -> None:
    schedules_by_level = {
        level: StoredSchedule(cron, enabled=True)
        for level, cron in default_schedules(app_config).items()
    }

    assert nonstandard_schedule_alert(app_config, schedules_by_level) is None
