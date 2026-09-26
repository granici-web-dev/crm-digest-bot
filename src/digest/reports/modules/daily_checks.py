import pandas as pd

from digest.metrics.daily_checks import (
    anomalies,
    overdue_revenire_by_manager,
    same_weekday_comparison,
    stale_offers,
    untouched_leads,
)
from digest.reports.context import ModuleResult, ReportContext
from digest.reports.render import render


def untouched_leads_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    untouched = untouched_leads(lead_frame, context.report_date, context.config)
    return ModuleResult(render("untouched_leads", untouched=untouched))


def overdue_revenire_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    overdue = overdue_revenire_by_manager(lead_frame, context.report_date, context.config)
    return ModuleResult(render("overdue_revenire", overdue=overdue))


def stale_offers_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    offers = stale_offers(lead_frame, context.previous, context.report_date, context.config)
    return ModuleResult(
        render(
            "stale_offers",
            offers=offers,
            stale_days=context.config.kpi.active_offer_stale_days,
        )
    )


def anomalies_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    found = anomalies(lead_frame, context.report_date, context.config)
    return ModuleResult(render("anomalies", anomalies=found))


def same_weekday_compare_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    comparison = same_weekday_comparison(lead_frame, context.report_date, context.config)
    return ModuleResult(render("same_weekday_compare", comparison=comparison))
