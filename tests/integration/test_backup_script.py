import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

BACKUP_SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "backup.sh"

FAKE_PG_DUMP = """#!/bin/sh
for argument in "$@"; do
    case "$argument" in
        --file=*) echo "dump" > "${argument#--file=}" ;;
    esac
done
exit "${FAKE_PG_DUMP_EXIT_CODE:-0}"
"""


@pytest.fixture
def backup_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "backups"
    directory.mkdir()
    return directory


@pytest.fixture
def fake_bin(tmp_path: Path) -> Path:
    directory = tmp_path / "bin"
    directory.mkdir()
    pg_dump = directory / "pg_dump"
    pg_dump.write_text(FAKE_PG_DUMP)
    pg_dump.chmod(0o755)
    return directory


def run_backup_once(
    backup_dir: Path, fake_bin: Path, pg_dump_exit_code: int = 0
) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "BACKUP_DIR": str(backup_dir),
        "FAKE_PG_DUMP_EXIT_CODE": str(pg_dump_exit_code),
    }
    return subprocess.run(
        ["sh", str(BACKUP_SCRIPT), "once"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def todays_dump_name() -> str:
    return f"digest-{datetime.now(UTC):%Y-%m-%d}.dump"


def make_file_aged(path: Path, age: timedelta) -> None:
    path.write_text("old")
    modified_at = (datetime.now(UTC) - age).timestamp()
    os.utime(path, (modified_at, modified_at))


def test_backup_creates_todays_dump(backup_dir: Path, fake_bin: Path) -> None:
    result = run_backup_once(backup_dir, fake_bin)

    assert result.returncode == 0
    assert sorted(path.name for path in backup_dir.iterdir()) == [todays_dump_name()]


def test_second_run_same_day_does_not_dump_again(backup_dir: Path, fake_bin: Path) -> None:
    todays_dump = backup_dir / todays_dump_name()
    todays_dump.write_text("first run")

    result = run_backup_once(backup_dir, fake_bin)

    assert result.returncode == 0
    assert todays_dump.read_text() == "first run"


def test_dumps_older_than_30_days_are_deleted_and_newer_kept(
    backup_dir: Path, fake_bin: Path
) -> None:
    expired_dump = backup_dir / "digest-2026-08-01.dump"
    kept_dump = backup_dir / "digest-2026-08-28.dump"
    make_file_aged(expired_dump, timedelta(days=31, hours=1))
    make_file_aged(kept_dump, timedelta(days=29))

    result = run_backup_once(backup_dir, fake_bin)

    assert result.returncode == 0
    assert not expired_dump.exists()
    assert kept_dump.exists()


def test_foreign_files_are_not_deleted(backup_dir: Path, fake_bin: Path) -> None:
    foreign_files = [backup_dir / "local-before-migration.dump", backup_dir / "notes.txt"]
    for foreign_file in foreign_files:
        make_file_aged(foreign_file, timedelta(days=90))

    result = run_backup_once(backup_dir, fake_bin)

    assert result.returncode == 0
    assert all(foreign_file.exists() for foreign_file in foreign_files)


def test_failed_pg_dump_leaves_no_dump_and_exits_nonzero(backup_dir: Path, fake_bin: Path) -> None:
    result = run_backup_once(backup_dir, fake_bin, pg_dump_exit_code=1)

    assert result.returncode != 0
    assert list(backup_dir.iterdir()) == []
