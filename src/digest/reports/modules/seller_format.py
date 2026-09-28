import pandas as pd

from digest.metrics.daily import seller_format_counts
from digest.reports.context import ModuleResult, ReportContext
from digest.reports.render import render


def seller_format_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    counts = seller_format_counts(
        lead_frame, context.previous, context.clients, context.report_date, context.config
    )
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
            f"({len(counts.missing_from_previous_lead_ids)}), в Oferta и переходы "
            f"Designer/Colaboratori не вошли, id: {list(counts.missing_from_previous_lead_ids)}."
        )
    if not counts.has_clients_snapshot:
        # Сбой источника I не блокирует отчёт (инвариант 6): «—» только в строке Contract.
        alerts.append(
            f"d1: нет успешного снапшота клиентов mefi за {context.report_date:%d.%m.%Y}, "
            "Contract Cantitate «—»."
        )
    text = render(
        "seller_format_report",
        counts=counts,
        report_date=context.report_date,
        tenant_display_name=context.config.status_mapping.tenant_display_name,
    )
    # Reoferta и Încasări берутся только из Oferte/Contracte (источник B, CLAUDE.md).
    return ModuleResult(text, tuple(alerts), unavailable_sources=("B",))
