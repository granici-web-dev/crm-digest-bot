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
    links = context.lead_links.block_lines(
        untouched.lead_ids, [group.lead_ids for group in untouched.groups]
    )
    return ModuleResult(render("untouched_leads", untouched=untouched, links=links))


def overdue_revenire_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    overdue = overdue_revenire_by_manager(lead_frame, context.report_date, context.config)
    links = context.lead_links.block_lines(
        overdue.lead_ids, [group.lead_ids for group in overdue.groups]
    )
    return ModuleResult(render("overdue_revenire", overdue=overdue, links=links))


def stale_offers_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    offers = stale_offers(lead_frame, context.previous, context.report_date, context.config)
    showrooms = list(offers.lead_ids_by_showroom)
    lines = context.lead_links.block_lines(
        offers.lead_ids, [offers.lead_ids_by_showroom[showroom] for showroom in showrooms]
    )
    return ModuleResult(
        render(
            "stale_offers",
            offers=offers,
            stale_days=context.config.kpi.active_offer_stale_days,
            links=dict(zip(showrooms, lines, strict=True)),
        )
    )


def anomalies_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    found = anomalies(lead_frame, context.report_date, context.config)
    return ModuleResult(render("anomalies", anomalies=found))


def same_weekday_compare_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    comparison = same_weekday_comparison(lead_frame, context.report_date, context.config)
    return ModuleResult(render("same_weekday_compare", comparison=comparison))
