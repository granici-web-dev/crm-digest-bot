from dataclasses import fields
from typing import Any

import pytest
from pydantic import ValidationError

from digest.app import create_report_deps
from digest.config import AppConfig
from digest.db.engine import create_database_engine
from digest.settings import Settings
from fakes import TEST_BOT_TOKEN

GROUP_CHAT_ID = -1001
TEST_CHAT_ID = -1002
OPS_CHAT_ID = -1003


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": "postgresql+asyncpg://digest:secret@localhost:5432/digest",
        "mefi_base_url": "https://example.test/api/v1",
        "mefi_api_key": "key",
        "tenant_id": "sofabelle",
        "telegram_bot_token": TEST_BOT_TOKEN,
        "telegram_ops_bot_token": TEST_BOT_TOKEN,
        "telegram_group_chat_id": GROUP_CHAT_ID,
        "telegram_test_chat_id": TEST_CHAT_ID,
        "telegram_ops_chat_id": OPS_CHAT_ID,
        **overrides,
    }
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize(
    "overrides",
    [
        {"telegram_test_chat_id": GROUP_CHAT_ID},
        {"telegram_ops_chat_id": GROUP_CHAT_ID},
        {"telegram_ops_chat_id": TEST_CHAT_ID},
    ],
)
def test_coinciding_chat_ids_fail_settings_load(overrides: dict[str, int]) -> None:
    with pytest.raises(ValidationError, match="chat_id должны различаться"):
        make_settings(**overrides)


@pytest.mark.parametrize(("dry_run", "chat_id"), [(True, TEST_CHAT_ID), (False, GROUP_CHAT_ID)])
def test_report_deps_carry_only_the_chosen_chat(
    app_config: AppConfig, dry_run: bool, chat_id: int
) -> None:
    app_settings = make_settings()
    engine = create_database_engine(app_settings.database_url.get_secret_value())

    deps = create_report_deps(engine, app_config, app_settings, dry_run)

    assert deps.report_chat_id == chat_id
    other_chat_id = GROUP_CHAT_ID if dry_run else TEST_CHAT_ID
    assert other_chat_id not in [getattr(deps, field.name) for field in fields(deps)]
