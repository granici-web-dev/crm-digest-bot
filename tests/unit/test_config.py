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
from factories import raw_repository_config

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


def repository_yaml(file_name: str) -> Any:
    return read_yaml(CONFIG_DIR / file_name)


def test_repository_config_maps_statuses_to_categories(app_config: AppConfig) -> None:
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


def test_showroom_visit_status_must_be_a_mapped_status() -> None:
    raw_mapping = repository_yaml("status-mapping.yaml")
    raw_mapping["sources"]["showroom_visit_status"] = "VIZITA"

    with pytest.raises(ValidationError, match="showroom_visit_status"):
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


def test_spi_module_cannot_be_enabled_while_kpi_is_provisional() -> None:
    raw_config = raw_repository_config()
    raw_config["modules"]["monthly"]["m6"]["enabled"] = True

    with pytest.raises(ValidationError, match="m6"):
        AppConfig.model_validate(raw_config)


def test_spi_module_can_be_enabled_once_kpi_is_calibrated() -> None:
    raw_config = raw_repository_config()
    raw_config["modules"]["monthly"]["m6"]["enabled"] = True
    raw_config["kpi"]["status"] = "calibrated"

    assert AppConfig.model_validate(raw_config).modules.monthly["m6"].enabled


def test_unknown_kpi_status_fails_config_load() -> None:
    raw_kpi = repository_yaml("kpi.yaml")
    raw_kpi["status"] = "final"

    with pytest.raises(ValidationError, match="status"):
        KpiSettings.model_validate(raw_kpi)


def test_target_with_unknown_threshold_fails_config_load() -> None:
    raw_kpi = repository_yaml("kpi.yaml")
    raw_kpi["targets"]["l2o"]["threshold"] = "l2o_tinta"

    with pytest.raises(ValidationError, match="l2o_tinta"):
        KpiSettings.model_validate(raw_kpi)


def test_target_value_comes_from_thresholds(app_config: AppConfig) -> None:
    assert app_config.kpi.target_value("plr") == app_config.kpi.thresholds["plr_maxim"]


def test_missing_target_fails_config_load() -> None:
    raw_kpi = repository_yaml("kpi.yaml")
    del raw_kpi["targets"]["o2c"]

    with pytest.raises(ValidationError, match="o2c"):
        KpiSettings.model_validate(raw_kpi)


def test_missing_score_steps_fail_config_load() -> None:
    raw_kpi = repository_yaml("kpi.yaml")
    del raw_kpi["scores"]["acr"]

    with pytest.raises(ValidationError, match="scores"):
        KpiSettings.model_validate(raw_kpi)


@pytest.mark.parametrize(
    ("kpi_name", "steps"),
    [
        ("scr", [["scr_minim", 30], ["scr_bine", 24], ["scr_elite", 18]]),
        ("plr", [["plr_problema", 20], ["plr_maxim", 16], ["plr_perfect", 10]]),
        ("scr", [["scr_elite", 18], ["scr_bine", 24], ["scr_minim", 30]]),
    ],
    ids=["higher_ascending", "lower_descending", "points_ascending"],
)
def test_non_monotonic_score_steps_fail_config_load(kpi_name: str, steps: list[Any]) -> None:
    raw_kpi = repository_yaml("kpi.yaml")
    raw_kpi["scores"][kpi_name]["steps"] = steps

    with pytest.raises(ValidationError, match=kpi_name):
        KpiSettings.model_validate(raw_kpi)


def test_target_direction_must_match_scores() -> None:
    raw_kpi = repository_yaml("kpi.yaml")
    raw_kpi["targets"]["plr"]["direction"] = "higher"

    with pytest.raises(ValidationError, match=r"targets\.plr"):
        KpiSettings.model_validate(raw_kpi)


@pytest.mark.parametrize(
    "comparison", [{}, {"above": "irr_atentie", "below": "irr_acceptabil"}], ids=["none", "both"]
)
def test_recommendation_needs_exactly_one_comparison(comparison: dict[str, str]) -> None:
    raw_kpi = repository_yaml("kpi.yaml")
    raw_kpi["recommendations"][0] = {"key": "irr_status_check", "kpi": "irr", **comparison}

    with pytest.raises(ValidationError, match="irr_status_check"):
        KpiSettings.model_validate(raw_kpi)


def test_not_taken_manager_must_be_inactive() -> None:
    raw_managers = repository_yaml("managers.yaml")
    marketing = next(manager for manager in raw_managers["managers"] if manager["id"] == 7)
    marketing["active"] = True

    with pytest.raises(ValidationError, match="not_taken"):
        ManagerRoster.model_validate(raw_managers)


def test_repository_marks_marketing_sofa_as_not_taken(app_config: AppConfig) -> None:
    assert app_config.managers.not_taken_ids == {7}


@pytest.mark.parametrize(
    ("module_id", "params"),
    [
        ("d2", {"threshold_hours": 4}),
        ("d2", {"threshold_hours": 0, "lookback_days": 3}),
        ("d5", {"site_zero_min_average": 2, "irelevant_spike_min": "five"}),
        ("d5", {"site_zero_min_average": 2, "irelevant_spike_min": 5, "web_zero": 1}),
    ],
)
def test_module_params_are_validated_at_load(module_id: str, params: dict[str, Any]) -> None:
    raw_modules = repository_yaml("modules.yaml")
    raw_modules["daily"][module_id]["params"] = params

    with pytest.raises(ValidationError, match=f"модуль {module_id}"):
        ModuleRegistry.model_validate(raw_modules)


def test_repository_module_params(app_config: AppConfig) -> None:
    untouched = app_config.modules.untouched_leads_params()
    anomalies = app_config.modules.anomaly_params()

    assert (untouched.threshold_hours, untouched.lookback_days) == (4, 3)
    assert (anomalies.site_zero_min_average, anomalies.irelevant_spike_min) == (2, 5)


def test_new_sources_are_in_groups(app_config: AppConfig) -> None:
    sources = app_config.status_mapping.sources
    assert "FacebookMessanger" in sources.web
    assert "BIFE 2026" in sources.other
