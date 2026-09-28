import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer

from conftest import REPOSITORY_ROOT, alembic_config
from digest.db.schema import metadata


def differences_from_schema_metadata(connection: Connection) -> list[object]:
    return list(compare_metadata(MigrationContext.configure(connection), metadata))


async def recreate_database(server_url: str, database_name: str) -> None:
    engine = create_async_engine(server_url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as connection:
        await connection.execute(text(f"DROP DATABASE IF EXISTS {database_name}"))
        await connection.execute(text(f"CREATE DATABASE {database_name}"))
    await engine.dispose()


async def schema_state(database_url: str) -> tuple[list[object], list[str]]:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        differences = await connection.run_sync(differences_from_schema_metadata)
        tenant_ids = list((await connection.execute(text("SELECT id FROM tenants"))).scalars())
    await engine.dispose()
    return differences, tenant_ids


def test_migrations_upgrade_from_zero(postgres_container: PostgresContainer) -> None:
    server_url = postgres_container.get_connection_url(driver="asyncpg")
    fresh_database_url = (
        make_url(server_url).set(database="migration_check").render_as_string(hide_password=False)
    )
    asyncio.run(recreate_database(server_url, "migration_check"))

    command.upgrade(alembic_config(fresh_database_url), "head")

    differences, tenant_ids = asyncio.run(schema_state(fresh_database_url))
    assert differences == []
    assert tenant_ids == ["sofabelle"]


def test_alembic_upgrade_uses_only_database_url(
    postgres_container: PostgresContainer, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server_url = postgres_container.get_connection_url(driver="asyncpg")
    fresh_database_url = (
        make_url(server_url).set(database="env_only_check").render_as_string(hide_password=False)
    )
    asyncio.run(recreate_database(server_url, "env_only_check"))
    # Без .env в рабочем каталоге и без токенов ботов: миграция видит только DATABASE_URL.
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith(("TELEGRAM_", "MEFI_", "TENANT_", "ANTHROPIC_")):
            monkeypatch.delenv(name)
    monkeypatch.setenv("DATABASE_URL", fresh_database_url)
    config = Config(REPOSITORY_ROOT / "alembic.ini")
    config.attributes["configure_logging"] = False

    command.upgrade(config, "head")

    differences, _tenant_ids = asyncio.run(schema_state(fresh_database_url))
    assert differences == []


async def execute_statements(database_url: str, statements: list[str]) -> list[str]:
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))
        statuses = list(
            (
                await connection.execute(text("SELECT status FROM snapshot_runs ORDER BY id"))
            ).scalars()
        )
    await engine.dispose()
    return statuses


def test_success_started_before_window_end_becomes_preview(
    postgres_container: PostgresContainer,
) -> None:
    server_url = postgres_container.get_connection_url(driver="asyncpg")
    database_url = (
        make_url(server_url).set(database="preview_check").render_as_string(hide_password=False)
    )
    asyncio.run(recreate_database(server_url, "preview_check"))
    command.upgrade(alembic_config(database_url), "0005")
    asyncio.run(
        execute_statements(
            database_url,
            [
                "INSERT INTO snapshot_runs (tenant_id, snapshot_date, attempt, status, started_at) "
                "VALUES "
                "('sofabelle', '2026-09-27', 1, 'success', '2026-09-27 19:00:00+03'), "
                "('sofabelle', '2026-09-28', 1, 'success', '2026-09-28 12:06:41+03'), "
                "('sofabelle', '2026-09-26', 1, 'failed', '2026-09-26 12:00:00+03')"
            ],
        )
    )

    command.upgrade(alembic_config(database_url), "head")

    assert asyncio.run(execute_statements(database_url, [])) == ["success", "preview", "failed"]


async def fetch_values(database_url: str, query: str) -> list[object]:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        values = list((await connection.execute(text(query))).scalars())
    await engine.dispose()
    return values


def test_stored_raw_keeps_only_whitelisted_custom_fields(
    postgres_container: PostgresContainer,
) -> None:
    server_url = postgres_container.get_connection_url(driver="asyncpg")
    database_url = (
        make_url(server_url).set(database="raw_whitelist").render_as_string(hide_password=False)
    )
    asyncio.run(recreate_database(server_url, "raw_whitelist"))
    command.upgrade(alembic_config(database_url), "0006")
    lead_raw = (
        '{"id": 1, "custom_fields": ['
        '{"field_id": 14, "name": "Showroom", "value": "Cluj"}, '
        '{"field_id": 7, "name": "Informatii", "value": "sunati +40711111111"}, '
        '{"field_id": 51, "name": "Mesaj", "value": "+40711111111"}, '
        '{"field_id": 20, "name": "Ofertat", "value": "✅DA"}]}'
    )
    client_raw = (
        '{"id": 2, "custom_fields": ['
        '{"field_id": 13, "name": "Informatii", "value": "+40711111111"}, '
        '{"field_id": 15, "name": "Showroom", "value": "Cluj"}]}'
    )
    asyncio.run(
        execute_statements(
            database_url,
            [
                "INSERT INTO lead_snapshots (tenant_id, snapshot_date, lead_id, category, "
                "created_at, raw) VALUES "
                f"('sofabelle', '2026-09-27', 1, 'ACTIVE', '2026-09-27 12:00:00+03', '{lead_raw}'),"
                "('sofabelle', '2026-09-27', 3, 'ACTIVE', '2026-09-27 12:00:00+03', "
                """'{"id": 3, "custom_fields": "+40711111111"}'),"""
                "('sofabelle', '2026-09-27', 4, 'ACTIVE', '2026-09-27 12:00:00+03', "
                """'{"id": 4, "custom_fields": null}')""",
                "INSERT INTO client_snapshots (tenant_id, snapshot_date, client_id, created_at, "
                f"raw) VALUES ('sofabelle', '2026-09-27', 2, '2026-09-27 12:00:00+03', "
                f"'{client_raw}')",
            ],
        )
    )

    command.upgrade(alembic_config(database_url), "head")

    lead_fields = asyncio.run(
        fetch_values(
            database_url, "SELECT raw -> 'custom_fields' FROM lead_snapshots ORDER BY lead_id"
        )
    )
    client_fields = asyncio.run(
        fetch_values(database_url, "SELECT raw -> 'custom_fields' FROM client_snapshots")
    )
    assert lead_fields == [
        [
            {"field_id": 14, "name": "Showroom", "value": "Cluj"},
            {"field_id": 20, "name": "Ofertat", "value": "✅DA"},
        ],
        None,
        None,
    ]
    assert client_fields == [[{"field_id": 15, "name": "Showroom", "value": "Cluj"}]]


def test_stored_lead_raw_keeps_only_known_keys_without_detailed_reason(
    postgres_container: PostgresContainer,
) -> None:
    server_url = postgres_container.get_connection_url(driver="asyncpg")
    database_url = (
        make_url(server_url).set(database="raw_known_keys").render_as_string(hide_password=False)
    )
    asyncio.run(recreate_database(server_url, "raw_known_keys"))
    command.upgrade(alembic_config(database_url), "0007")
    unknown_key_raw = '{"id": 1, "whatsapp_number": "+40711111111", "source": {"id": 6}}'
    elimination_raw = (
        '{"id": 2, "elimination": {"type": "lost", "reason": {"id": 3, "name": "BUGET"}, '
        '"detailed_reason": "sunati +40711111111", "marked_at": "2026-09-23T09:00:00Z"}}'
    )
    asyncio.run(
        execute_statements(
            database_url,
            [
                "INSERT INTO lead_snapshots (tenant_id, snapshot_date, lead_id, category, "
                "created_at, raw) VALUES "
                "('sofabelle', '2026-09-27', 1, 'ACTIVE', '2026-09-27 12:00:00+03', "
                f"'{unknown_key_raw}'), "
                "('sofabelle', '2026-09-27', 2, 'LOST', '2026-09-27 12:00:00+03', "
                f"'{elimination_raw}'), "
                "('sofabelle', '2026-09-27', 3, 'ACTIVE', '2026-09-27 12:00:00+03', "
                """'{"id": 3, "status": {"id": 16}}')""",
            ],
        )
    )

    command.upgrade(alembic_config(database_url), "head")

    stored_raw = asyncio.run(
        fetch_values(database_url, "SELECT raw FROM lead_snapshots ORDER BY lead_id")
    )
    assert stored_raw == [
        {"id": 1, "source": {"id": 6}},
        {
            "id": 2,
            "elimination": {
                "type": "lost",
                "reason": {"id": 3, "name": "BUGET"},
                "marked_at": "2026-09-23T09:00:00Z",
            },
        },
        {"id": 3, "status": {"id": 16}},
    ]


def test_snapshot_runs_get_trigger_and_missed_status(postgres_container: PostgresContainer) -> None:
    server_url = postgres_container.get_connection_url(driver="asyncpg")
    database_url = (
        make_url(server_url).set(database="snapshot_trigger").render_as_string(hide_password=False)
    )
    asyncio.run(recreate_database(server_url, "snapshot_trigger"))
    command.upgrade(alembic_config(database_url), "0008")
    asyncio.run(
        execute_statements(
            database_url,
            [
                "INSERT INTO snapshot_runs (tenant_id, snapshot_date, attempt, status, error) "
                "VALUES "
                "('sofabelle', '2026-09-25', 1, 'success', NULL), "
                "('sofabelle', '2026-09-26', 1, 'failed', 'missed'), "
                "('sofabelle', '2026-09-27', 1, 'failed', 'superseded')"
            ],
        )
    )

    command.upgrade(alembic_config(database_url), "head")

    rows = asyncio.run(
        fetch_values(
            database_url,
            "SELECT row(status, attempt, trigger, error)::text FROM snapshot_runs ORDER BY id",
        )
    )
    assert rows == [
        "(success,1,scheduled,)",
        "(missed,,scheduled,)",
        "(failed,1,scheduled,superseded)",
    ]
