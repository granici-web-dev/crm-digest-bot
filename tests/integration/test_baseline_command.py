from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from test_report_runner import REPORT_DATE, TENANT_ID, store_snapshot, todays_lead

from digest.__main__ import run_baseline
from digest.acceptance.baseline import parse_baseline_lines
from digest.settings import Settings
from fakes import TEST_BOT_TOKEN


def settings_for(database_url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=database_url,
        mefi_base_url="https://example.test/api/v1",
        mefi_api_key="key",
        mefi_clients_api_key="clients-key",
        contact_hash_key="k" * 32,
        tenant_id=TENANT_ID,
        telegram_bot_token=TEST_BOT_TOKEN,
        telegram_ops_bot_token=TEST_BOT_TOKEN,
        telegram_group_chat_id=-1001,
        telegram_test_chat_id=-1002,
        telegram_ops_chat_id=-1003,
        telegram_admin_ids=[5001],
    )


async def test_baseline_command_writes_file_for_snapshot_date(
    engine: AsyncEngine, database_url: str, tmp_path: Path
) -> None:
    await store_snapshot(engine, REPORT_DATE - timedelta(days=1), [todays_lead(1)])
    await store_snapshot(engine, REPORT_DATE, [todays_lead(1), todays_lead(2)])
    out = tmp_path / "baseline.md"

    exit_code = await run_baseline(settings_for(database_url), REPORT_DATE, out, None)

    assert exit_code == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# Baseline 25.09.2026"
    rows = {row.id: row for row in parse_baseline_lines(lines)}
    assert rows["A5.untouched.cur_average"].period == "2 из 25 дней, сентябрь 2026"
    assert rows["B1.useful.cur"].value == 2


async def test_baseline_compare_prints_delta(
    engine: AsyncEngine,
    database_url: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await store_snapshot(engine, REPORT_DATE, [todays_lead(1), todays_lead(2)])
    base_file = tmp_path / "base.md"
    base_file.write_text(
        "| id | Метрика | Значение | Ед. | Период |\n"
        "|---|---|---|---|---|\n"
        "| B1.useful.cur | Полезные лиды когорты | 5 | лидов | август 2026 |\n",
        encoding="utf-8",
    )
    base = parse_baseline_lines(base_file.read_text(encoding="utf-8").splitlines())

    exit_code = await run_baseline(settings_for(database_url), REPORT_DATE, None, (base_file, base))

    assert exit_code == 0
    assert "| B1.useful.cur | Полезные лиды когорты | 5 | 2 | -3 |" in capsys.readouterr().out


async def test_baseline_without_snapshot_exits_2(
    engine: AsyncEngine, database_url: str, tmp_path: Path
) -> None:
    await store_snapshot(engine, REPORT_DATE - timedelta(days=1), [todays_lead(1)])
    out = tmp_path / "baseline.md"

    exit_code = await run_baseline(settings_for(database_url), REPORT_DATE, out, None)

    assert exit_code == 2
    assert not out.exists()
