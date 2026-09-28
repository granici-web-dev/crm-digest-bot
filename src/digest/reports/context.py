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
    # Снапшот ровно за прошлое воскресенье, для оферт w8.
    week_ago: PreviousSnapshot | None
    # Снапшот прошлой недели для w6: воскресенье или первый более поздний строго раньше
    # report_date, дата в нём.
    previous_week: PreviousSnapshot | None
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
