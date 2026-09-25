import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from digest.backup_check import stale_backup_alert

NOW = datetime(2026, 9, 25, 16, 30, tzinfo=UTC)


def write_dump(backup_dir: Path, name: str, age: timedelta) -> None:
    dump = backup_dir / name
    dump.write_text("dump")
    modified_at = (NOW - age).timestamp()
    os.utime(dump, (modified_at, modified_at))


def test_fresh_dump_gives_no_alert(tmp_path: Path) -> None:
    write_dump(tmp_path, "digest-2026-09-25.dump", timedelta(hours=16))

    assert stale_backup_alert(tmp_path, NOW) is None


def test_dump_exactly_26_hours_old_is_still_fresh(tmp_path: Path) -> None:
    write_dump(tmp_path, "digest-2026-09-24.dump", timedelta(hours=26))

    assert stale_backup_alert(tmp_path, NOW) is None


def test_dump_older_than_26_hours_gives_alert_with_name_and_age(tmp_path: Path) -> None:
    write_dump(tmp_path, "digest-2026-09-24.dump", timedelta(hours=26, minutes=1))

    alert = stale_backup_alert(tmp_path, NOW)

    assert alert is not None
    assert "digest-2026-09-24.dump" in alert
    assert "26 ч назад" in alert


def test_freshest_dump_decides_when_older_ones_exist(tmp_path: Path) -> None:
    write_dump(tmp_path, "digest-2026-09-01.dump", timedelta(days=24))
    write_dump(tmp_path, "digest-2026-09-25.dump", timedelta(hours=2))

    assert stale_backup_alert(tmp_path, NOW) is None


def test_empty_backup_dir_gives_alert(tmp_path: Path) -> None:
    alert = stale_backup_alert(tmp_path, NOW)

    assert alert is not None
    assert "нет ни одного дампа" in alert


def test_partial_and_foreign_files_do_not_count_as_dumps(tmp_path: Path) -> None:
    write_dump(tmp_path, "digest-2026-09-25.dump.partial", timedelta(minutes=5))
    write_dump(tmp_path, "local-before-migration.dump", timedelta(minutes=5))
    write_dump(tmp_path, "digest-2026-09-20.dump", timedelta(days=5))

    alert = stale_backup_alert(tmp_path, NOW)

    assert alert is not None
    assert "digest-2026-09-20.dump" in alert
