import asyncio

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from testcontainers.community.postgres import PostgresContainer

from conftest import alembic_config
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
