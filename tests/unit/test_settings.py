from dataclasses import fields
from typing import Any

import pytest
from pydantic import ValidationError

from digest.app import chat_ids, create_anthropic_client, create_report_deps
from digest.config import AppConfig
from digest.db.engine import create_database_engine
from digest.settings import Settings
from fakes import TEST_BOT_TOKEN

GROUP_CHAT_ID = -1001
TEST_CHAT_ID = -1002
OPS_CHAT_ID = -1003
ADMIN_ID = 5001


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": "postgresql+asyncpg://digest:secret@localhost:5432/digest",
        "mefi_base_url": "https://example.test/api/v1",
        "mefi_api_key": "key",
        "mefi_clients_api_key": "clients-key",
        "contact_hash_key": "k" * 32,
        "tenant_id": "sofabelle",
        "telegram_bot_token": TEST_BOT_TOKEN,
        "telegram_ops_bot_token": TEST_BOT_TOKEN,
        "telegram_group_chat_id": GROUP_CHAT_ID,
        "telegram_test_chat_id": TEST_CHAT_ID,
        "telegram_ops_chat_id": OPS_CHAT_ID,
        "telegram_admin_ids": [ADMIN_ID],
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


def test_short_contact_hash_key_fails_settings_load() -> None:
    with pytest.raises(ValidationError, match="CONTACT_HASH_KEY короче 32"):
        make_settings(contact_hash_key="k" * 31)


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


def test_admin_ids_are_read_from_comma_separated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_ADMIN_IDS", "5001, 5002")
    values = make_settings().model_dump()
    del values["telegram_admin_ids"]

    assert Settings(_env_file=None, **values).telegram_admin_ids == [5001, 5002]


def test_empty_admin_ids_fail_settings_load() -> None:
    with pytest.raises(ValidationError, match="telegram_admin_ids"):
        make_settings(telegram_admin_ids="")


def test_chat_answers_prod_group_only_without_dry_run() -> None:
    assert chat_ids(make_settings(dry_run=True)) == {TEST_CHAT_ID}
    assert chat_ids(make_settings(dry_run=False)) == {TEST_CHAT_ID, GROUP_CHAT_ID}


def test_chat_without_api_key_has_no_client() -> None:
    assert create_anthropic_client(make_settings()) is None
    assert create_anthropic_client(make_settings(anthropic_api_key="key")) is not None
    assert make_settings().anthropic_model == "claude-sonnet-5"
