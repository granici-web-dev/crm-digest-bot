from collections.abc import Callable

import pandas as pd

from digest.reports.context import ModuleResult, ReportContext
from digest.reports.modules.daily_checks import (
    anomalies_report,
    overdue_revenire_report,
    same_weekday_compare_report,
    stale_offers_report,
    untouched_leads_report,
)
from digest.reports.modules.seller_format import seller_format_report
from digest.reports.modules.weekly import (
    loss_reasons_report,
    showroom_visits_report,
    week_over_week_report,
    weekly_funnel_report,
    weekly_leads_report,
)
from digest.reports.modules.weekly_excel import excel_attachment_report

ReportModuleFunction = Callable[[pd.DataFrame, ReportContext], ModuleResult]

IMPLEMENTED_MODULES: dict[str, ReportModuleFunction] = {
    "d1": seller_format_report,
    "d2": untouched_leads_report,
    "d3": overdue_revenire_report,
    "d4": stale_offers_report,
    "d5": anomalies_report,
    "d6": same_weekday_compare_report,
    "w1": weekly_leads_report,
    "w2": showroom_visits_report,
    "w3": weekly_funnel_report,
    "w4": loss_reasons_report,
    "w8": week_over_week_report,
    "w12": excel_attachment_report,
}
