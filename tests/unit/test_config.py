from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from digest.config import (
    AppConfig,
    KpiSettings,
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


def test_score_step_with_unknown_threshold_fails_config_load() -> None:
    raw_kpi = repository_yaml("kpi.yaml")
    raw_kpi["scores"]["scr"]["steps"][0][0] = "scr_elit"

    with pytest.raises(ValidationError, match="scr_elit"):
        KpiSettings.model_validate(raw_kpi)


def test_spi_levels_must_descend_to_zero() -> None:
    raw_kpi = repository_yaml("kpi.yaml")
    raw_kpi["levels"] = raw_kpi["levels"][:-1]

    with pytest.raises(ValidationError, match="levels"):
        KpiSettings.model_validate(raw_kpi)


@pytest.mark.parametrize(
    "reason_name", ["IRELEVANT", "NU_RASPUNS", "BUGET", "PRODUS_NEPOTRIVIT", "STAND_BY"]
)
def test_missing_loss_reason_used_by_metrics_fails_config_load(reason_name: str) -> None:
    raw_mapping = repository_yaml("status-mapping.yaml")
    del raw_mapping["categories"]["LOST"]["reasons"][reason_name]

    with pytest.raises(ValidationError, match=reason_name):
        StatusMapping.model_validate(raw_mapping)


def test_missing_partnership_category_fails_config_load() -> None:
    raw_mapping = repository_yaml("status-mapping.yaml")
    del raw_mapping["categories"]["PARTNERSHIP"]

    with pytest.raises(ValidationError, match="PARTNERSHIP"):
        StatusMapping.model_validate(raw_mapping)


def test_missing_showroom_visit_sources_fails_config_load() -> None:
    raw_mapping = repository_yaml("status-mapping.yaml")
    del raw_mapping["sources"]["showroom_visit"]

    with pytest.raises(ValidationError, match="showroom_visit"):
        StatusMapping.model_validate(raw_mapping)


def test_threshold_outside_zero_to_one_fails_config_load() -> None:
    raw_kpi = repository_yaml("kpi.yaml")
    raw_kpi["thresholds"]["scr_elite"] = 10

    with pytest.raises(ValidationError, match="scr_elite"):
        KpiSettings.model_validate(raw_kpi)


@pytest.mark.parametrize("days", [0, -14])
def test_non_positive_stale_days_fails_config_load(days: int) -> None:
    raw_kpi = repository_yaml("kpi.yaml")
    raw_kpi["active_offer_stale_days"] = days

    with pytest.raises(ValidationError, match="active_offer_stale_days"):
        KpiSettings.model_validate(raw_kpi)
