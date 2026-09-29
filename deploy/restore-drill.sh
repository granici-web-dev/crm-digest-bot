#!/bin/sh
# Репетиция восстановления из бэкапа (ворота M9, docs/success-criteria.md). Запуск из корня
# репозитория на ноутбуке при поднятом `docker compose up -d postgres`. В базу источника
# ничего не пишет: дамп, копия во временном контейнере, сверка, рендер отчёта по копии.
# --keep оставляет временную базу для учебного сбоя M7 (docs/acceptance/m7-source-failure.md).
set -eu

DRILL_CONTAINER=digest-restore-drill
DRILL_PORT=55432
DRILL_PASSWORD=drill
DRILL_URL="postgresql+asyncpg://digest:$DRILL_PASSWORD@127.0.0.1:$DRILL_PORT/digest"
KEEP=0
if [ "${1:-}" = "--keep" ]; then
    KEEP=1
fi

WORK_DIR=$(mktemp -d)
IN_CONTAINER_DIR=/tmp/digest-restore-drill
RESULTS="$WORK_DIR/results"
: > "$RESULTS"
DRILL_STARTED=$(date +%s)

cleanup() {
    docker compose exec -T postgres rm -rf "$IN_CONTAINER_DIR" > /dev/null 2>&1 || true
    if [ "$KEEP" -eq 0 ]; then
        docker rm -f "$DRILL_CONTAINER" > /dev/null 2>&1 || true
        rm -rf "$WORK_DIR"
    fi
}
trap cleanup EXIT

print_results() {
    echo
    echo "| шаг | секунды | итог |"
    echo "|---|---|---|"
    cat "$RESULTS"
    echo "Всего: $(( $(date +%s) - DRILL_STARTED )) с"
}

# Внутри `if` set -e не действует: каждая команда шага проверяется явно через `|| return 1`.
run_step() {
    step_name=$1
    shift
    step_started=$(date +%s)
    if "$@"; then
        step_result=ok
    else
        step_result=FAIL
    fi
    echo "| $step_name | $(( $(date +%s) - step_started )) | $step_result |" >> "$RESULTS"
    if [ "$step_result" = FAIL ]; then
        print_results
        exit 1
    fi
}

source_sql() {
    docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -XAtc "$1"' _ "$1"
}

copy_sql() {
    docker exec "$DRILL_CONTAINER" psql -U digest -d digest -XAtc "$1"
}

dump_with_backup_script() {
    # Тот же deploy/backup.sh и тот же образ postgres:16, что пишут бэкапы в проде; скрипт
    # подаётся через stdin, чтобы не монтировать deploy/ в локальный контейнер.
    docker compose exec -T postgres sh -c "mkdir -p $IN_CONTAINER_DIR && \
        BACKUP_DIR=$IN_CONTAINER_DIR PGHOST=localhost PGUSER=\"\$POSTGRES_USER\" \
        PGPASSWORD=\"\$POSTGRES_PASSWORD\" PGDATABASE=\"\$POSTGRES_DB\" sh -s once" \
        < deploy/backup.sh || return 1
    dump_name=$(docker compose exec -T postgres sh -c "ls $IN_CONTAINER_DIR" | grep '^digest-.*\.dump$') \
        || return 1
    docker compose cp "postgres:$IN_CONTAINER_DIR/$dump_name" "$WORK_DIR/drill.dump" > /dev/null 2>&1 \
        || return 1
    echo "Дамп: $(du -h "$WORK_DIR/drill.dump" | cut -f1)"
}

start_drill_postgres() {
    docker rm -f "$DRILL_CONTAINER" > /dev/null 2>&1 || true
    docker run -d --name "$DRILL_CONTAINER" -e POSTGRES_USER=digest -e POSTGRES_DB=digest \
        -e POSTGRES_PASSWORD="$DRILL_PASSWORD" -p "127.0.0.1:$DRILL_PORT:5432" postgres:16 > /dev/null \
        || return 1
    # По TCP: временный сервер initdb слушает только сокет, готовность по сокету была бы ложной.
    attempts=0
    until docker exec "$DRILL_CONTAINER" pg_isready -h 127.0.0.1 -U digest -d digest > /dev/null 2>&1; do
        attempts=$((attempts + 1))
        if [ "$attempts" -gt 60 ]; then
            echo "временный Postgres не поднялся за 60 с" >&2
            return 1
        fi
        sleep 1
    done
}

restore_dump() {
    # Флаги из docs/deploy.md §5, без --clean: база пустая.
    docker exec -i "$DRILL_CONTAINER" pg_restore --no-owner --single-transaction --exit-on-error \
        -U digest -d digest < "$WORK_DIR/drill.dump"
}

database_facts() {
    run_sql=$1
    for table in $($run_sql "select tablename from pg_tables where schemaname = 'public' order by 1"); do
        echo "$table $($run_sql "select count(*) from public.\"$table\"")"
    done
    echo "alembic_version $($run_sql "select version_num from alembic_version")"
    echo "last_success_snapshot $($run_sql \
        "select coalesce(max(snapshot_date)::text, 'нет') from snapshot_runs where status = 'success'")"
}

compare_with_source() {
    database_facts source_sql > "$WORK_DIR/source.txt"
    database_facts copy_sql > "$WORK_DIR/copy.txt"
    # Упавший psql внутри $(...) дал бы пустые значения в обоих файлах и ложное совпадение.
    if ! grep -q '^alembic_version [0-9]' "$WORK_DIR/source.txt"; then
        echo "не удалось прочитать счётчики источника" >&2
        return 1
    fi
    echo "Источник и копия (таблица, строк):"
    sed 's/^/  /' "$WORK_DIR/source.txt"
    if ! diff "$WORK_DIR/source.txt" "$WORK_DIR/copy.txt"; then
        echo "Копия расходится с источником (строки выше: < источник, > копия)" >&2
        return 1
    fi
}

upgrade_copy() {
    # Так после восстановления делает entrypoint контейнера app.
    DATABASE_URL="$DRILL_URL" uv run alembic upgrade head
}

render_report_from_copy() {
    last_success=$(copy_sql "select max(snapshot_date) from snapshot_runs where status = 'success'")
    if [ -z "$last_success" ]; then
        echo "в копии нет успешного снапшота" >&2
        return 1
    fi
    DATABASE_URL="$DRILL_URL" uv run python -m digest audit privacy --days 1 --end "$last_success"
}

run_step "дамп deploy/backup.sh" dump_with_backup_script
run_step "временный Postgres" start_drill_postgres
run_step "pg_restore" restore_dump
run_step "сверка счётчиков" compare_with_source
run_step "alembic upgrade head" upgrade_copy
run_step "отчёт по копии без отправки" render_report_from_copy
print_results
if [ "$KEEP" -eq 1 ]; then
    echo "Временная база оставлена: DATABASE_URL=$DRILL_URL"
    echo "Удалить: docker rm -f $DRILL_CONTAINER"
fi
