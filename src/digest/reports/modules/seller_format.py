import pandas as pd

from digest.metrics.daily import seller_format_counts
from digest.reports.context import ModuleResult, ReportContext
from digest.reports.render import render


def seller_format_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    counts = seller_format_counts(lead_frame, context.previous, context.report_date, context.config)
    alerts: list[str] = []
    if counts.unknown_source_lead_ids:
        alerts.append(
            f"d1: лиды с источником вне групп sources в config/status-mapping.yaml "
            f"({len(counts.unknown_source_lead_ids)}), посчитаны в Alte, "
            f"id: {list(counts.unknown_source_lead_ids)}."
        )
    if counts.missing_from_previous_lead_ids:
        alerts.append(
            f"d1: лиды старше окна, которых нет во вчерашнем снапшоте "
            f"({len(counts.missing_from_previous_lead_ids)}), в Vizita/Oferta/Contract не вошли, "
            f"id: {list(counts.missing_from_previous_lead_ids)}."
        )
    text = render(
        "seller_format_report", context.language, counts=counts, report_date=context.report_date
    )
    # Reoferta и Încasări берутся только из Oferte/Contracte (источник B, CLAUDE.md).
    return ModuleResult(text, tuple(alerts), unavailable_sources=("B",))
