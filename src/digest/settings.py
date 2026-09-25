from pathlib import Path
from typing import Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    # Миграциям нужен только DATABASE_URL: без токенов ботов alembic тоже должен работать.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    database_url: SecretStr


class Settings(BaseSettings):
    # env_ignore_empty: пустое DRY_RUN= из .env.example значит «не задано», а не ошибка bool.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    database_url: SecretStr
    mefi_base_url: str
    mefi_api_key: SecretStr
    tenant_id: str
    telegram_bot_token: SecretStr
    telegram_ops_bot_token: SecretStr
    telegram_group_chat_id: int
    telegram_test_chat_id: int
    telegram_ops_chat_id: int
    dry_run: bool = False
    log_level: str = "INFO"
    # APP_VERSION задаёт образ (git sha из build-аргумента), вне образа версия "dev".
    app_version: str = "dev"
    # BACKUP_DIR задаёт только docker-compose.prod.yml: вне прода дампов нет и проверять нечего.
    backup_dir: Path | None = None

    def report_chat_id(self, dry_run: bool) -> int:
        return self.telegram_test_chat_id if dry_run else self.telegram_group_chat_id

    @model_validator(mode="after")
    def chat_ids_are_distinct(self) -> Self:
        # Совпадение test с group отправило бы DRY_RUN в продовую группу, ops с group отправил бы
        # туда алерты.
        chat_ids = {
            "TELEGRAM_GROUP_CHAT_ID": self.telegram_group_chat_id,
            "TELEGRAM_TEST_CHAT_ID": self.telegram_test_chat_id,
            "TELEGRAM_OPS_CHAT_ID": self.telegram_ops_chat_id,
        }
        if len(set(chat_ids.values())) != len(chat_ids):
            raise ValueError(f"chat_id должны различаться: {', '.join(chat_ids)}")
        return self
