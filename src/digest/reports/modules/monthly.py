from collections.abc import Hashable
from datetime import date
from typing import Any

import pandas as pd

from digest.config import KPI_NAMES, AppConfig
from digest.metrics.cockpit import manager_cockpit_table
from digest.metrics.monthly import (
    BELOW_ALL_LEVELS,
    month_window,
    monthly_funnel,
    monthly_loss_reasons,
    monthly_scr,
    monthly_trend,
)
from digest.reports.charts import chart_labels, funnel_chart, trend_chart
from digest.reports.context import ModuleResult, ReportContext, ReportPhoto
from digest.reports.modules.weekly import WITHOUT_SHOWROOM
from digest.reports.render import ReportLanguage, render, target_label

# m5 в Telegram: девять KPI не помещаются в одну строку <code> на телефоне.
COCKPIT_KPI_LINES = (KPI_NAMES[:5], KPI_NAMES[5:])


def month_file_suffix(report_date: date) -> str:
    return f"{report_date:%Y-%m}"


def funnel_by_showroom_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    funnel = monthly_funnel(lead_frame, context.report_date, context.config)
    labels = chart_labels(context.language)
    without_showroom = funnel.without_showroom
    alerts: tuple[str, ...] = ()
    if without_showroom.useful:
        # По агрегату снапшота все лиды без шоурума IRELEVANT (docs/PLAN.md); полезный лид без
        # поля Showroom выпадает из воронок шоурумов, это вопрос качества данных в mefi.
        alerts = (
            f"m2 за {funnel.month:%m.%Y}: {without_showroom.useful} полезных лидов без шоурума "
            f"(всего без шоурума {without_showroom.leads}). Заполнить поле Showroom в mefi.",
        )
    return ModuleResult(
        render(
            "funnel_by_showroom",
            context.language,
            funnel=funnel,
            without_showroom_note=labels.without_showroom_note(without_showroom),
            clienti_note=labels.clienti_note,
        ),
        alerts=alerts,
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
            month_names=[labels.month_name(month) for month in trend.months],
        ),
        photo=ReportPhoto(
            f"trend_{month_file_suffix(context.report_date)}.png", trend_chart(trend, labels)
        ),
    )


def target_labels(config: AppConfig) -> dict[str, str]:
    return {
        kpi_name: target_label(
            config.kpi.target_value(kpi_name), config.kpi.targets[kpi_name].direction
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
            showrooms=funnel.showrooms_with_leads,
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
            kpi_count=len(KPI_NAMES),
            kpi_lines=COCKPIT_KPI_LINES,
            targets=target_labels(context.config),
            clienti_note=chart_labels(context.language).clienti_note,
        )
    )


def loss_reason_labels(config: AppConfig, language: ReportLanguage) -> dict[str, str]:
    return {
        key: reason.label_ro if language == "ro" else reason.label_ru
        for key, reason in config.status_mapping.categories.LOST.reasons.items()
    }


def loss_reasons_trend_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    losses = monthly_loss_reasons(lead_frame, context.report_date, context.config)
    labels = chart_labels(context.language)
    return ModuleResult(
        render(
            "loss_reasons_trend",
            context.language,
            losses=losses,
            reason_labels=loss_reason_labels(context.config, context.language),
            month_name=labels.month_name(losses.month),
            previous_month_name=labels.month_name(losses.previous_month),
        )
    )
