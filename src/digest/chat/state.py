from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from digest.config import AppConfig
from digest.db.schema import settings

CHAT_ENABLED_KEY = "chat_enabled"


async def chat_enabled(engine: AsyncEngine, tenant_id: str, config: AppConfig) -> bool:
    async with engine.connect() as connection:
        stored = await connection.scalar(
            select(settings.c.value).where(
                settings.c.tenant_id == tenant_id, settings.c.key == CHAT_ENABLED_KEY
            )
        )
    return config.modules.chat.enabled if stored is None else bool(stored)


async def store_chat_enabled(
    engine: AsyncEngine, tenant_id: str, enabled: bool, updated_by: int
) -> None:
    values = {"value": enabled, "updated_by": updated_by}
    async with engine.begin() as connection:
        await connection.execute(
            pg_insert(settings)
            .values(tenant_id=tenant_id, key=CHAT_ENABLED_KEY, **values)
            .on_conflict_do_update(
                index_elements=[settings.c.tenant_id, settings.c.key],
                set_={**values, "updated_at": func.now()},
            )
        )
