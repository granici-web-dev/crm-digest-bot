from datetime import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from digest.config import (
    AppConfig,
    CohortConversionParams,
    KpiSettings,
    LeadCategory,
    ManagerRoster,
    ModuleBlockers,
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
    raw_config = raw_repository_config()
    raw_config["modules"]["monthly"]["m10"]["enabled"] = True

    with pytest.raises(ValidationError, match="m10"):
        AppConfig.model_validate(raw_config)


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


def test_loss_reason_without_label_fails_config_load() -> None:
    raw_mapping = repository_yaml("status-mapping.yaml")
    del raw_mapping["categories"]["LOST"]["reasons"]["TIMP"]["label"]

    with pytest.raises(ValidationError, match="label"):
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


def test_client_raw_strip_outside_known_keys_fails_config_load() -> None:
    raw_mapping = repository_yaml("status-mapping.yaml")
    raw_mapping["clients"]["raw_strip"].append("passport.number")

    with pytest.raises(ValidationError, match=r"clients.raw_strip \['passport'\]"):
        StatusMapping.model_validate(raw_mapping)


def test_lead_raw_strip_outside_known_keys_fails_config_load() -> None:
    raw_mapping = repository_yaml("status-mapping.yaml")
    raw_mapping["raw_strip"].append("passport.number")

    with pytest.raises(ValidationError, match=r"raw_strip \['passport'\] нет в raw_known_keys"):
        StatusMapping.model_validate(raw_mapping)


def test_lead_nested_keys_outside_known_keys_fail_config_load() -> None:
    raw_mapping = repository_yaml("status-mapping.yaml")
    raw_mapping["raw_known_nested_keys"]["passport"] = ["number"]

    with pytest.raises(ValidationError, match=r"raw_known_nested_keys \['passport'\]"):
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


@pytest.mark.parametrize("days", [[], [90, 30], [30, 30], [0, 30]])
def test_backlog_age_days_must_be_positive_ascending(days: list[int]) -> None:
    raw_kpi = repository_yaml("kpi.yaml")
    raw_kpi["backlog_age_days"] = days

    with pytest.raises(ValidationError, match="backlog_age_days"):
        KpiSettings.model_validate(raw_kpi)


def test_baseline_cohort_days_must_be_one_of_age_buckets() -> None:
    with pytest.raises(ValidationError, match="baseline_cohort_days: 45 нет в age_buckets_days"):
        CohortConversionParams.model_validate(
            {"age_buckets_days": [7, 30, 90], "fast_cycle_days": 7, "baseline_cohort_days": 45}
        )


def test_repository_scr_levels_reference_kpi_thresholds(app_config: AppConfig) -> None:
    levels = app_config.modules.scr_levels_params.levels

    assert [level.threshold for level in levels] == ["scr_elite", "scr_bine", "scr_minim"]
    assert [level.label for level in levels] == ["Elită", "Bine", "Minim"]


def scr_levels(*thresholds: str) -> list[dict[str, str]]:
    return [{"threshold": threshold, "label": f"label {threshold}"} for threshold in thresholds]


@pytest.mark.parametrize(
    ("levels", "message"),
    [
        (scr_levels("scr_elite", "scr_top"), "scr_top"),
        (scr_levels("scr_bine", "scr_elite"), "строго убывать"),
        (scr_levels("scr_elite", "scr_elite"), "строго убывать"),
    ],
)
def test_scr_levels_must_be_known_descending_thresholds(
    levels: list[dict[str, str]], message: str
) -> None:
    raw_config = raw_repository_config()
    raw_config["modules"]["monthly"]["m4"]["params"]["levels"] = levels

    with pytest.raises(ValidationError, match=message):
        AppConfig.model_validate(raw_config)


@pytest.mark.parametrize("label", ["Elite", "gold", "COACHING"])
def test_scr_level_label_cannot_repeat_spi_level_name(label: str) -> None:
    raw_config = raw_repository_config()
    raw_config["modules"]["monthly"]["m4"]["params"]["levels"][0]["label"] = label

    with pytest.raises(ValidationError, match="уровнями SPI"):
        AppConfig.model_validate(raw_config)


def test_scr_levels_must_not_be_empty() -> None:
    raw_modules = repository_yaml("modules.yaml")
    raw_modules["monthly"]["m4"]["params"]["levels"] = []

    with pytest.raises(ValidationError, match="модуль m4"):
        ModuleRegistry.model_validate(raw_modules)


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


def test_module_blockers_name_disconnected_sources_and_missing_kpi_status(
    app_config: AppConfig,
) -> None:
    assert app_config.module_blockers("m13").disconnected_sources == ("B", "D")
    assert app_config.module_blockers("m6").missing_kpi_status == "calibrated"
    assert app_config.module_blockers("d1") == ModuleBlockers(
        disconnected_sources=(), missing_kpi_status=None
    )


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
        (
            "d5",
            {"site_zero_min_average": 2, "site_average_days": 7, "irelevant_spike_min": "five"},
        ),
        (
            "d5",
            {
                "site_zero_min_average": 2,
                "site_average_days": 7,
                "irelevant_spike_min": 5,
                "web_zero": 1,
            },
        ),
        ("d5", {"site_zero_min_average": 2, "site_average_days": 0, "irelevant_spike_min": 5}),
        ("d7", {"window_days": 0, "trend_threshold_pp": 2.0}),
        ("d7", {"window_days": 30, "trend_threshold_pp": 0}),
    ],
)
def test_module_params_are_validated_at_load(module_id: str, params: dict[str, Any]) -> None:
    raw_modules = repository_yaml("modules.yaml")
    raw_modules["daily"][module_id]["params"] = params

    with pytest.raises(ValidationError, match=f"модуль {module_id}"):
        ModuleRegistry.model_validate(raw_modules)


def test_repository_module_params(app_config: AppConfig) -> None:
    untouched = app_config.modules.untouched_leads_params
    anomalies = app_config.modules.anomaly_params

    assert (untouched.threshold_hours, untouched.lookback_days) == (4, 3)
    assert untouched.touch_tolerance_seconds == 60
    assert (anomalies.site_zero_min_average, anomalies.irelevant_spike_min) == (2, 5)
    assert anomalies.site_average_days == 7
    rolling = app_config.modules.rolling_contract_rate_params
    assert (rolling.window_days, rolling.trend_threshold_pp) == (30, 2.0)


def test_module_with_params_must_be_in_registry() -> None:
    raw_modules = repository_yaml("modules.yaml")
    del raw_modules["daily"]["d5"]

    with pytest.raises(ValidationError, match="модуль anomalies не найден"):
        ModuleRegistry.model_validate(raw_modules)


def test_new_sources_are_in_groups(app_config: AppConfig) -> None:
    sources = app_config.status_mapping.sources
    assert "FacebookMessanger" in sources.web
    assert "BIFE 2026" in sources.other
    assert sources.repeat_client == ["Client Fidel"]
    assert "Client Fidel" not in sources.other


def test_site_sources_must_be_web() -> None:
    raw_mapping = repository_yaml("status-mapping.yaml")
    raw_mapping["sources"]["site"] = ["Telefon"]

    with pytest.raises(ValidationError, match="Telefon"):
        StatusMapping.model_validate(raw_mapping)


@pytest.mark.parametrize(
    "group_name", ["showroom_visit", "phone", "whatsapp", "partner", "other", "repeat_client"]
)
def test_source_in_two_row_groups_fails_config_load(group_name: str) -> None:
    raw_mapping = repository_yaml("status-mapping.yaml")
    raw_mapping["sources"][group_name].append("Meta ADS")

    with pytest.raises(ValidationError, match="источник 'Meta ADS' и в sources") as error:
        StatusMapping.model_validate(raw_mapping)
    assert f"sources.{group_name}" in str(error.value)


def test_custom_field_both_kept_and_dropped_fails_config_load() -> None:
    raw_mapping = repository_yaml("status-mapping.yaml")
    raw_mapping["clients"]["raw_custom_fields"]["keep"].append(13)

    with pytest.raises(ValidationError, match=r"field_id \[13\] и в keep, и в drop"):
        StatusMapping.model_validate(raw_mapping)


def test_utm_campanie_missing_from_raw_keep_fails_config_load() -> None:
    raw_mapping = repository_yaml("status-mapping.yaml")
    raw_mapping["raw_custom_fields"]["keep"].remove(39)

    with pytest.raises(
        ValidationError, match=r"UTM_Campanie \(field_id 39\) нет в raw_custom_fields"
    ):
        StatusMapping.model_validate(raw_mapping)


def test_module_label_longer_than_28_characters_fails_config_load() -> None:
    raw_modules = repository_yaml("modules.yaml")
    raw_modules["daily"]["d1"]["label"] = "Raport automat în formatul consilierilor"

    with pytest.raises(ValidationError, match="28"):
        ModuleRegistry.model_validate(raw_modules)


@pytest.mark.parametrize("too_early", ["19:10", "19:29"])
def test_daily_send_time_before_snapshot_is_ready_fails_config_load(too_early: str) -> None:
    raw_config = raw_repository_config()
    raw_config["modules"]["send_times"]["daily"] = [too_early, "20:00"]

    with pytest.raises(ValidationError, match=f"{too_early}.*19:30"):
        AppConfig.model_validate(raw_config)


def test_repeated_send_time_fails_config_load() -> None:
    raw_modules = repository_yaml("modules.yaml")
    raw_modules["send_times"]["weekly"] = ["09:00", "09:00"]

    with pytest.raises(ValidationError, match=r"send_times\.weekly"):
        ModuleRegistry.model_validate(raw_modules)


def test_weekly_send_times_without_default_option_fail_config_load() -> None:
    raw_modules = repository_yaml("modules.yaml")
    raw_modules["send_times"]["weekly"] = ["09:00"]

    with pytest.raises(ValidationError, match=r"send_times\.weekly: время по умолчанию"):
        ModuleRegistry.model_validate(raw_modules)


def test_default_send_times_come_from_send_times(app_config: AppConfig) -> None:
    assert app_config.modules.default_send_time("daily") == time(19, 30)
    assert app_config.modules.default_send_time("weekly") == time(9, 0)
    assert app_config.modules.default_send_time("monthly") == time(9, 0)


@pytest.mark.parametrize(
    "path",
    [
        "/admin/leads/index/",
        "admin/leads/{lead_id}",
        "/leads/{lead_id}/{lead_id}",
        "/{x}/{lead_id}",
    ],
)
def test_lead_link_path_without_single_lead_id_placeholder_fails_config_load(path: str) -> None:
    raw_config = raw_repository_config()
    raw_config["status_mapping"]["lead_links"]["path"] = path

    with pytest.raises(ValidationError, match=r"lead_links\.path"):
        AppConfig.model_validate(raw_config)
