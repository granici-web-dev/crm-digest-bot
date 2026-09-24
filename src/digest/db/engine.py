from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def create_database_engine(database_url: str, **engine_options: Any) -> AsyncEngine:
    # Без hide_parameters текст ошибки insert содержит строки raw, то есть данные клиентов.
    return create_async_engine(database_url, hide_parameters=True, **engine_options)
