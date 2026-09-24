from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer

from digest.config import AppConfig, load_app_config

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TABLES_TRUNCATED_BETWEEN_TESTS = (
    "lead_snapshots",
    "snapshot_runs",
    "settings",
    "module_settings",
    "schedules",
    "report_runs",
)


def alembic_config(database_url: str) -> Config:
    config = Config(REPOSITORY_ROOT / "alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    config.attributes["configure_logging"] = False
    return config


@pytest.fixture(scope="session")
def app_config() -> AppConfig:
    return load_app_config(REPOSITORY_ROOT / "config")


@pytest.fixture(scope="session")
def postgres_container() -> Iterator[PostgresContainer]:
    with PostgresContainer("postgres:16-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def database_url(postgres_container: PostgresContainer) -> str:
    url: str = postgres_container.get_connection_url(driver="asyncpg")
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.begin() as connection:
        await connection.execute(
            text(f"TRUNCATE {', '.join(TABLES_TRUNCATED_BETWEEN_TESTS)} RESTART IDENTITY")
        )
    yield engine
    await engine.dispose()
