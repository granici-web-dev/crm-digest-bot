from dataclasses import replace
from typing import Any

import pytest

from digest.config import AppConfig, KpiSettings, RecommendationRule, ScoreSteps
from digest.metrics.kpi import Kpis, LeadCounts
from digest.metrics.spi import irr_penalty, kpi_scores, recommendation_key, spi, spi_level

BEST_KPIS = Kpis(scr=0.2, l2o=0.6, o2c=0.3, cdr=1.0, plr=0.1, sc=0.3, pfr=0.05, acr=0.1, irr=0.1)
NO_KPIS = Kpis(
    scr=None, l2o=None, o2c=None, cdr=None, plr=None, sc=None, pfr=None, acr=None, irr=None
)


def healthy_counts(**overrides: int) -> LeadCounts:
    counts = {
        "leads": 100,
        "irr_leads": 10,
        "useful": 90,
        "clienti": 10,
        "offers": 50,
        "nar": 2,
        "buget": 10,
        "pnp": 5,
        "showroom_visits": 20,
        "clienti_from_showroom": 5,
        "active_offers_14": 5,
        "unmapped": 0,
    }
    counts.update(overrides)
    return LeadCounts(**counts)


@pytest.mark.parametrize(
    ("kpi_name", "value", "points"),
    [
        ("scr", 0.10, 30),
        ("scr", 0.0999, 24),
        ("scr", 0.07, 24),
        ("scr", 0.05, 18),
        ("scr", 0.0499, 10),
        ("scr", None, 10),
        ("cdr", 0.95, 20),
        ("cdr", 0.90, 16),
        ("cdr", 0.80, 10),
        ("cdr", 0.7999, 5),
        ("cdr", None, 5),
        ("plr", 0.20, 20),
        ("plr", 0.2001, 16),
        ("plr", 0.35, 10),
        ("plr", 0.3501, 5),
        ("plr", None, 5),
        ("sc", 0.15, 12),
        ("sc", 0.0999, 4),
        ("sc", None, 4),
        ("pfr", 0.15, 8),
        ("pfr", 0.2001, 2),
        ("pfr", None, 2),
        ("acr", 0.30, 3),
        ("acr", 0.3001, 1),
        ("acr", None, 1),
    ],
)
def test_score_is_first_step_met_and_lowest_for_null(
    app_config: AppConfig, kpi_name: str, value: float | None, points: int
) -> None:
    kpis = replace(BEST_KPIS, **{kpi_name: value})

    assert kpi_scores(kpis, app_config.kpi)[kpi_name] == points  # type: ignore[index]


@pytest.mark.parametrize(
    ("irr", "penalty"), [(0.20, 0), (0.2001, -5), (0.30, -5), (0.3001, -10), (None, -10)]
)
def test_irr_penalty_steps(app_config: AppConfig, irr: float | None, penalty: int) -> None:
    assert irr_penalty(replace(BEST_KPIS, irr=irr), app_config.kpi) == penalty


def test_spi_is_100_when_every_kpi_is_at_best(app_config: AppConfig) -> None:
    assert spi(BEST_KPIS, app_config.kpi) == 100


def test_spi_without_data_is_sum_of_lowest_steps(app_config: AppConfig) -> None:
    assert spi(NO_KPIS, app_config.kpi) == 10 + 5 + 5 + 4 + 2 + 1 - 10


def test_spi_is_never_negative(app_config: AppConfig) -> None:
    harsh_penalty = ScoreSteps(direction="lower", steps=[], otherwise=-1000)
    settings = app_config.kpi.model_copy(update={"irr_penalty": harsh_penalty})

    assert spi(NO_KPIS, settings) == 0


@pytest.mark.parametrize(
    ("spi_value", "level"),
    [
        (100, "Elite"),
        (90, "Elite"),
        (89, "Gold"),
        (70, "Silver"),
        (60, "Bronze"),
        (59, "Coaching"),
        (0, "Coaching"),
    ],
)
def test_spi_level_by_lower_bound(app_config: AppConfig, spi_value: int, level: str) -> None:
    assert spi_level(spi_value, app_config.kpi) == level


@pytest.mark.parametrize(
    ("overrides", "key"),
    [
        ({"irr_leads": 31, "useful": 69, "nar": 20}, "irr_status_check"),
        ({"irr_leads": 30, "useful": 70, "nar": 20}, "contact_discipline"),
        ({"buget": 30}, "price_value"),
        ({"clienti": 4, "clienti_from_showroom": 4}, "closing"),
        ({"active_offers_14": 21}, "active_offers_followup"),
        ({}, "stable_performance"),
    ],
)
def test_recommendation_is_first_rule_met(
    app_config: AppConfig, overrides: dict[str, Any], key: str
) -> None:
    assert recommendation_key(healthy_counts(**overrides), app_config.kpi) == key


def test_no_recommendation_without_leads(app_config: AppConfig) -> None:
    zero = LeadCounts(**dict.fromkeys(healthy_counts().__dataclass_fields__, 0))

    assert recommendation_key(zero, app_config.kpi) is None


def test_null_kpi_does_not_trigger_its_rule(app_config: AppConfig) -> None:
    closing_only: KpiSettings = app_config.kpi.model_copy(
        update={
            "recommendations": [RecommendationRule(key="closing", kpi="scr", below="scr_minim")]
        }
    )
    all_irrelevant = healthy_counts(irr_leads=100, useful=0, clienti=0, clienti_from_showroom=0)

    assert recommendation_key(all_irrelevant, closing_only) == "stable_performance"
