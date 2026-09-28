from dataclasses import dataclass
from datetime import date

import pandas as pd

from digest.config import AppConfig, SourceCode
from digest.metrics.daily import PreviousSnapshot
from digest.reports.lead_links import LeadLinks


@dataclass(frozen=True)
class ReportContext:
    report_date: date
    previous: PreviousSnapshot | None
    week_ago: PreviousSnapshot | None
    # Клиенты mefi за report_date; None: снапшота клиентов нет или ни один модуль их не читает.
    clients: pd.DataFrame | None
    config: AppConfig
    tenant_id: str
    lead_links: LeadLinks


@dataclass(frozen=True)
class ReportDocument:
    filename: str
    content: bytes


@dataclass(frozen=True)
class ReportPhoto:
    filename: str
    content: bytes


@dataclass(frozen=True)
class ModuleResult:
    text: str
    alerts: tuple[str, ...] = ()
    unavailable_sources: tuple[SourceCode, ...] = ()
    photo: ReportPhoto | None = None
    document: ReportDocument | None = None
