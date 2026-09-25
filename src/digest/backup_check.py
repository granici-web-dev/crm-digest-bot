from datetime import UTC, datetime, timedelta
from pathlib import Path

# deploy/backup.sh пишет дамп раз в сутки по UTC при ежечасной проверке: свежайший дамп
# в норме не старше ~25 часов, 26 дают час запаса.
BACKUP_MAX_AGE = timedelta(hours=26)


def stale_backup_alert(backup_dir: Path, now: datetime) -> str | None:
    dumps = list(backup_dir.glob("digest-*.dump"))
    if not dumps:
        return (
            f"Бэкап: в {backup_dir} нет ни одного дампа digest-*.dump. "
            "Проверьте docker compose logs backup."
        )
    latest_dump = max(dumps, key=lambda dump: dump.stat().st_mtime)
    latest_dump_age = now - datetime.fromtimestamp(latest_dump.stat().st_mtime, UTC)
    if latest_dump_age <= BACKUP_MAX_AGE:
        return None
    age_hours = int(latest_dump_age.total_seconds() // 3600)
    max_age_hours = int(BACKUP_MAX_AGE.total_seconds() // 3600)
    return (
        f"Бэкап: последний дамп {latest_dump.name} создан {age_hours} ч назад, "
        f"допустимо {max_age_hours} ч. Проверьте docker compose logs backup."
    )
