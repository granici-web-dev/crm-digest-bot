from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    def report_chat_id(self, dry_run: bool) -> int:
        return self.telegram_test_chat_id if dry_run else self.telegram_group_chat_id
