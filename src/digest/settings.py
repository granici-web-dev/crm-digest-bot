from pathlib import Path
from typing import Annotated, Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CONTACT_HASH_KEY_MIN_LENGTH = 32


class DatabaseSettings(BaseSettings):
    # Миграциям нужен только DATABASE_URL: без токенов ботов alembic тоже должен работать.
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_ignore_empty=True, hide_input_in_errors=True
    )

    database_url: SecretStr


class Settings(BaseSettings):
    # env_ignore_empty: пустое DRY_RUN= из .env.example значит «не задано», а не ошибка bool.
    # hide_input_in_errors: SecretStr не маскирует input_value в тексте ValidationError,
    # и короткий CONTACT_HASH_KEY или битый токен попали бы в лог старта.
    model_config = SettingsConfigDict(
        env_file=".env", extra="ignore", env_ignore_empty=True, hide_input_in_errors=True
    )

    database_url: SecretStr
    mefi_base_url: str
    mefi_api_key: SecretStr
    mefi_clients_api_key: SecretStr
    # Секрет HMAC ключей контакта (digest.contact_keys). Сравнение идёт внутри одного снапшота,
    # поэтому смена секрета ничего не ломает.
    contact_hash_key: SecretStr
    tenant_id: str
    telegram_bot_token: SecretStr
    telegram_ops_bot_token: SecretStr
    telegram_group_chat_id: int
    telegram_test_chat_id: int
    telegram_ops_chat_id: int
    telegram_admin_ids: Annotated[list[int], NoDecode, Field(min_length=1)]
    dry_run: bool = False
    # Без ключа режим вопросов не включается (переключатель в /settings серый).
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-sonnet-5"
    log_level: str = "INFO"
    # APP_VERSION задаёт образ (git sha из build-аргумента), вне образа версия "dev".
    app_version: str = "dev"
    # BACKUP_DIR задаёт только docker-compose.prod.yml: вне прода дампов нет и проверять нечего.
    backup_dir: Path | None = None

    @field_validator("telegram_admin_ids", mode="before")
    @classmethod
    def split_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("contact_hash_key")
    @classmethod
    def contact_hash_key_is_long(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < CONTACT_HASH_KEY_MIN_LENGTH:
            raise ValueError(
                f"CONTACT_HASH_KEY короче {CONTACT_HASH_KEY_MIN_LENGTH} символов: "
                "сгенерировать openssl rand -hex 32"
            )
        return value

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
