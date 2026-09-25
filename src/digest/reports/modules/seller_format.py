import pandas as pd

from digest.metrics.daily import seller_format_counts
from digest.reports.context import ModuleResult, ReportContext
from digest.reports.render import render


def seller_format_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    counts = seller_format_counts(
        lead_frame, context.previous_frame, context.period, context.config
    )
    return ModuleResult(
        render(
            "seller_format_report", context.language, counts=counts, report_date=context.report_date
        )
    )
