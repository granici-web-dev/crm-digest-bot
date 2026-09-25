from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from digest.config import (
    AppConfig,
    LeadCategory,
    ManagerRoster,
    ModuleRegistry,
    StatusMapping,
    read_yaml,
)

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def repository_yaml(file_name: str) -> Any:
    return read_yaml(CONFIG_DIR / file_name)


def test_repository_config_loads(app_config: AppConfig) -> None:
    category_by_status = app_config.status_mapping.category_by_status
    assert category_by_status["Clienți"] == LeadCategory("WON")
    assert category_by_status["Revenire 2"] == LeadCategory("ACTIVE_FOLLOWUP")
    assert category_by_status["NU A RASPUNS"] == LeadCategory("LOST", "NU_RASPUNS")
    assert category_by_status["DESIGNER"] == LeadCategory("PARTNERSHIP")
    assert app_config.status_mapping.custom_fields.showroom.field_id == 14


def test_status_in_two_categories_fails_config_load() -> None:
    raw_mapping = repository_yaml("status-mapping.yaml")
    raw_mapping["categories"]["ACTIVE"]["statuses"].append("Clienți")

    with pytest.raises(ValidationError, match="Clienți"):
        StatusMapping.model_validate(raw_mapping)


def test_enabled_module_with_disconnected_source_fails_config_load() -> None:
    raw_registry = repository_yaml("modules.yaml")
    raw_registry["monthly"]["m10"]["enabled"] = True

    with pytest.raises(ValidationError, match="m10"):
        ModuleRegistry.model_validate(raw_registry)


def test_managers_yaml_rejects_duplicate_ids() -> None:
    raw_roster = repository_yaml("managers.yaml")
    raw_roster["managers"].append({"id": 8, "name": "Copie", "showroom": None, "active": True})

    with pytest.raises(ValidationError, match=r"\[8\]"):
        ManagerRoster.model_validate(raw_roster)


def test_manager_showroom_must_be_in_showrooms_list() -> None:
    raw_roster = repository_yaml("managers.yaml")
    raw_roster["managers"][0]["showroom"] = "Bucuresti"

    with pytest.raises(ValidationError, match="Bucuresti"):
        AppConfig.model_validate(
            {
                "status_mapping": repository_yaml("status-mapping.yaml"),
                "modules": repository_yaml("modules.yaml"),
                "managers": raw_roster,
                "kpi": repository_yaml("kpi.yaml"),
            }
        )
