import pandas as pd
from markupsafe import Markup

from digest.metrics.daily_checks import (
    anomalies,
    missing_followup_date,
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
    missing = None
    missing_links: list[Markup] = []
    alerts: tuple[str, ...] = ()
    if context.config.modules.overdue_revenire_params.missing_followup_date:
        missing = missing_followup_date(lead_frame, context.report_date, context.config)
        # Отдельный блок со своим лимитом ссылок (инвариант 7): иначе при 10+ просроченных он
        # их не получит.
        missing_links = context.lead_links.block_lines(
            missing.lead_ids, [group.lead_ids for group in missing.groups]
        )
        if missing.field_unavailable:
            alerts = (
                f"d3 за {context.report_date:%d.%m.%Y}: поле Data revenire не прочитано ни у "
                f"одного из {missing.unreadable_count} лидов Revenire/Stand BY (поля нет, "
                "переименовано или не дата), блок «Fără Data revenire» не посчитан. Проверить "
                "custom_fields.data_revenire в status-mapping.yaml.",
            )
    return ModuleResult(
        render(
            "overdue_revenire",
            overdue=overdue,
            links=links,
            missing=missing,
            missing_links=missing_links,
        ),
        alerts=alerts,
    )


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
