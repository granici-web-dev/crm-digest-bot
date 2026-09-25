# Предварительная механика (ADR-002): отчёты MVP и чат этот модуль не вызывают.
from dataclasses import asdict

from digest.config import KpiName, KpiSettings, ScoreSteps
from digest.metrics.kpi import Kpis, LeadCounts, kpis_from


def step_points(value: float | None, score_steps: ScoreSteps, settings: KpiSettings) -> int:
    # KPI = null получает нижнюю ступень (docs/kpi-definitions.md, «Предварительно»).
    if value is None:
        return score_steps.otherwise
    for threshold_name, points in score_steps.steps:
        threshold = settings.thresholds[threshold_name]
        met = value >= threshold if score_steps.direction == "higher" else value <= threshold
        if met:
            return points
    return score_steps.otherwise


def kpi_scores(kpis: Kpis, settings: KpiSettings) -> dict[KpiName, int]:
    values = asdict(kpis)
    return {
        kpi_name: step_points(values[kpi_name], score_steps, settings)
        for kpi_name, score_steps in settings.scores.items()
    }


def irr_penalty(kpis: Kpis, settings: KpiSettings) -> int:
    return step_points(kpis.irr, settings.irr_penalty, settings)


def spi(kpis: Kpis, settings: KpiSettings) -> int:
    return max(0, sum(kpi_scores(kpis, settings).values()) + irr_penalty(kpis, settings))


def spi_level(spi_value: int, settings: KpiSettings) -> str:
    return next(level.name for level in settings.levels if spi_value >= level.min_spi)


def recommendation_key(counts: LeadCounts, settings: KpiSettings) -> str | None:
    if counts.leads == 0:
        return None
    values = asdict(kpis_from(counts))
    for rule in settings.recommendations:
        value = values[rule.kpi]
        if value is None:
            continue
        if rule.above is not None and value > settings.thresholds[rule.above]:
            return rule.key
        if rule.below is not None and value < settings.thresholds[rule.below]:
            return rule.key
    return settings.recommendation_otherwise
