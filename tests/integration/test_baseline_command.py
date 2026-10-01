import asyncio
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from test_report_runner import REPORT_DATE, TENANT_ID, store_snapshot, todays_lead

from digest.__main__ import main
from digest.acceptance.baseline import parse_baseline_file
from fakes import TEST_BOT_TOKEN

# main() читает Settings из окружения; переменные окружения сильнее локального .env.
ENVIRONMENT = {
    "MEFI_BASE_URL": "https://example.test/api/v1",
    "MEFI_API_KEY": "key",
    "MEFI_CLIENTS_API_KEY": "clients-key",
    "CONTACT_HASH_KEY": "k" * 32,
    "TENANT_ID": TENANT_ID,
    "TELEGRAM_BOT_TOKEN": TEST_BOT_TOKEN,
    "TELEGRAM_OPS_BOT_TOKEN": TEST_BOT_TOKEN,
    "TELEGRAM_GROUP_CHAT_ID": "-1001",
    "TELEGRAM_TEST_CHAT_ID": "-1002",
    "TELEGRAM_OPS_CHAT_ID": "-1003",
    "TELEGRAM_ADMIN_IDS": "5001",
}


@pytest.fixture
def baseline_environment(
    engine: AsyncEngine, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncEngine:
    for name, value in {**ENVIRONMENT, "DATABASE_URL": database_url}.items():
        monkeypatch.setenv(name, value)
    return engine


def test_baseline_command_writes_file_for_snapshot_date(
    baseline_environment: AsyncEngine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    asyncio.run(
        store_snapshot(baseline_environment, REPORT_DATE - timedelta(days=1), [todays_lead(1)])
    )
    asyncio.run(store_snapshot(baseline_environment, REPORT_DATE, [todays_lead(1), todays_lead(2)]))
    out = tmp_path / "baseline.md"

    exit_code = main(["baseline", "--date", REPORT_DATE.isoformat(), "--out", str(out)])

    assert exit_code == 0
    assert out.read_text(encoding="utf-8").splitlines()[0] == "# Baseline 25.09.2026"
    rows = {row.id: row for row in parse_baseline_file(out)}
    assert rows["A5.untouched.cur_average"].period == "2 из 25 дней, сентябрь 2026"
    assert rows["B1.useful.cur"].value == 2
    printed = capsys.readouterr().out
    assert printed.startswith("Внимание: 25.09.2026 не последний день месяца.")
    assert "Внимание" not in out.read_text(encoding="utf-8")


def test_baseline_compare_prints_delta(
    baseline_environment: AsyncEngine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    asyncio.run(store_snapshot(baseline_environment, REPORT_DATE, [todays_lead(1), todays_lead(2)]))
    base_file = tmp_path / "base.md"
    base_file.write_text(
        "| id | Метрика | Значение | Ед. | Период |\n"
        "|---|---|---|---|---|\n"
        "| B1.useful.cur | Полезные лиды когорты | 5 | лидов | август 2026 |\n",
        encoding="utf-8",
    )

    exit_code = main(["baseline", "--date", REPORT_DATE.isoformat(), "--compare", str(base_file)])

    assert exit_code == 0
    assert "| B1.useful.cur | Полезные лиды когорты | 5 | 2 | -3 |" in capsys.readouterr().out


def test_baseline_compare_with_missing_file_exits_2_before_database(
    baseline_environment: AsyncEngine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "нет.md"

    exit_code = main(["baseline", "--date", REPORT_DATE.isoformat(), "--compare", str(missing)])

    assert exit_code == 2
    assert capsys.readouterr().out.startswith(f"--compare {missing}: файл не прочитан")


def test_baseline_compare_with_foreign_file_exits_2(
    baseline_environment: AsyncEngine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    foreign = tmp_path / "eval.md"
    foreign.write_text("| id | Вопрос | Итог |\n|---|---|---|\n", encoding="utf-8")

    exit_code = main(["baseline", "--date", REPORT_DATE.isoformat(), "--compare", str(foreign)])

    assert exit_code == 2
    assert "нет строки заголовка" in capsys.readouterr().out


def test_baseline_without_snapshot_exits_2(
    baseline_environment: AsyncEngine, tmp_path: Path
) -> None:
    asyncio.run(
        store_snapshot(baseline_environment, REPORT_DATE - timedelta(days=1), [todays_lead(1)])
    )
    out = tmp_path / "baseline.md"

    exit_code = main(["baseline", "--date", REPORT_DATE.isoformat(), "--out", str(out)])

    assert exit_code == 2
    assert not out.exists()
