import pytest
from sqlalchemy import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.db.schema import tenants


async def test_database_errors_hide_statement_parameters(engine: AsyncEngine) -> None:
    with pytest.raises(IntegrityError) as raised:
        async with engine.begin() as connection:
            await connection.execute(
                insert(tenants).values(id="sofabelle", name="CLIENT_TEST +40700000077")
            )

    assert "+40700000077" not in str(raised.value)
