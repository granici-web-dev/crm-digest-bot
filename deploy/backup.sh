#!/bin/sh
# Ежедневный pg_dump; удаляются дампы старше 30 полных суток (find -mtime +30). Работает в контейнере postgres:16
# (docker-compose.prod.yml), подключение через PGHOST, PGUSER, PGPASSWORD, PGDATABASE.
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
RETENTION_DAYS=30
CHECK_INTERVAL_SECONDS=3600

backup_once() {
    todays_dump="$BACKUP_DIR/digest-$(date -u +%Y-%m-%d).dump"
    if [ ! -e "$todays_dump" ]; then
        # Оборванный pg_dump не должен выглядеть сегодняшним дампом: пишем рядом и переименовываем.
        partial_dump="$todays_dump.partial"
        if ! pg_dump --format=custom --file="$partial_dump"; then
            rm -f "$partial_dump"
            echo "backup failed: pg_dump exited with an error" >&2
            return 1
        fi
        mv "$partial_dump" "$todays_dump"
        echo "backup written: $todays_dump"
    fi
    # .partial остаётся, только если контейнер убили посреди pg_dump; такие файлы тоже стареют и удаляются.
    find "$BACKUP_DIR" -maxdepth 1 -type f \( -name 'digest-*.dump' -o -name 'digest-*.dump.partial' \) \
        -mtime +"$RETENTION_DAYS" -delete
}

case "${1:-}" in
    once)
        backup_once
        ;;
    loop)
        # Ежечасная проверка «есть ли дамп за сегодня» переживает рестарты контейнера
        # без расчёта времени до следующего запуска.
        while true; do
            if ! backup_once; then
                echo "backup will be retried in ${CHECK_INTERVAL_SECONDS}s" >&2
            fi
            sleep "$CHECK_INTERVAL_SECONDS"
        done
        ;;
    *)
        echo "usage: backup.sh once|loop" >&2
        exit 2
        ;;
esac
