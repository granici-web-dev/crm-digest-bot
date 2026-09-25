from collections.abc import Hashable
from datetime import date
from typing import Any

import pandas as pd

from digest.config import KPI_NAMES, AppConfig
from digest.metrics.cockpit import manager_cockpit_table
from digest.metrics.monthly import (
    BELOW_ALL_LEVELS,
    MonthlyFunnel,
    month_window,
    monthly_funnel,
    monthly_loss_reasons,
    monthly_scr,
    monthly_trend,
)
from digest.reports.charts import chart_labels, funnel_chart, trend_chart
from digest.reports.context import ModuleResult, ReportContext, ReportPhoto
from digest.reports.modules.weekly import WITHOUT_SHOWROOM
from digest.reports.render import render

# m5 в Telegram: девять KPI не помещаются в одну строку <code> на телефоне.
COCKPIT_KPI_LINES = (KPI_NAMES[:5], KPI_NAMES[5:])


def month_file_suffix(report_date: date) -> str:
    return f"{report_date:%Y-%m}"


def showrooms_with_leads(funnel: MonthlyFunnel) -> list[str | None]:
    return [showroom for showroom, counts in funnel.by_showroom.items() if counts.leads]


def funnel_by_showroom_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    funnel = monthly_funnel(lead_frame, context.report_date, context.config)
    labels = chart_labels(context.language)
    return ModuleResult(
        render(
            "funnel_by_showroom",
            context.language,
            funnel=funnel,
            showrooms=showrooms_with_leads(funnel),
            without_showroom=WITHOUT_SHOWROOM,
            clienti_note=labels.clienti_note,
        ),
        photo=ReportPhoto(
            f"funnel_{month_file_suffix(context.report_date)}.png", funnel_chart(funnel, labels)
        ),
    )


def trend_6m_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    trend = monthly_trend(lead_frame, context.report_date, context.config)
    labels = chart_labels(context.language)
    return ModuleResult(
        render(
            "trend_6m",
            context.language,
            trend=trend,
            month_names=[labels.months[month.month - 1] for month in trend.months],
        ),
        photo=ReportPhoto(
            f"trend_{month_file_suffix(context.report_date)}.png", trend_chart(trend, labels)
        ),
    )


def target_labels(config: AppConfig) -> dict[str, str]:
    return {
        kpi_name: (
            f"{'≥' if config.kpi.targets[kpi_name].direction == 'higher' else '≤'}"
            f"{config.kpi.target_value(kpi_name) * 100:g}%"
        )
        for kpi_name in KPI_NAMES
    }


def scr_level_labels(context: ReportContext) -> dict[str, str]:
    params = context.config.modules.scr_levels_params
    romanian = context.language == "ro"
    return {
        **{
            level.threshold: level.label_ro if romanian else level.label_ru
            for level in params.levels
        },
        BELOW_ALL_LEVELS: params.below_label_ro if romanian else params.below_label_ru,
    }


def scr_with_targets_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    config = context.config
    funnel = monthly_funnel(lead_frame, context.report_date, config)
    return ModuleResult(
        render(
            "scr_with_targets",
            context.language,
            scr=monthly_scr(funnel, config),
            showrooms=showrooms_with_leads(funnel),
            levels=[
                (level.threshold, config.kpi.thresholds[level.threshold])
                for level in config.modules.scr_levels_params.levels
            ],
            level_labels=scr_level_labels(context),
            scr_target=target_labels(config)["scr"],
            without_showroom=WITHOUT_SHOWROOM,
        )
    )


def manager_cockpit_rows(
    lead_frame: pd.DataFrame, context: ReportContext
) -> list[dict[Hashable, Any]]:
    config = context.config
    window = month_window(context.report_date, config.status_mapping.time)
    table = manager_cockpit_table(lead_frame, window, context.report_date, config)
    return table.to_dict("records")


def manager_cockpit_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    return ModuleResult(
        render(
            "manager_cockpit",
            context.language,
            rows=manager_cockpit_rows(lead_frame, context),
            kpi_lines=COCKPIT_KPI_LINES,
            targets=target_labels(context.config),
            clienti_note=chart_labels(context.language).clienti_note,
        )
    )


def loss_reason_labels(context: ReportContext) -> dict[str, str]:
    return {
        key: reason.label_ro if context.language == "ro" else reason.label_ru
        for key, reason in context.config.status_mapping.categories.LOST.reasons.items()
    }


def loss_reasons_trend_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    losses = monthly_loss_reasons(lead_frame, context.report_date, context.config)
    months = chart_labels(context.language).months
    month = context.report_date.month
    return ModuleResult(
        render(
            "loss_reasons_trend",
            context.language,
            losses=losses,
            reason_labels=loss_reason_labels(context),
            month_name=months[month - 1],
            previous_month_name=months[(month - 2) % 12],
        )
    )
