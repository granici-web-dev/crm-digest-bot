from dataclasses import dataclass
from datetime import date

from digest.config import AppConfig, SourceCode
from digest.metrics.daily import PreviousSnapshot
from digest.reports.render import ReportLanguage


@dataclass(frozen=True)
class ReportContext:
    report_date: date
    previous: PreviousSnapshot | None
    config: AppConfig
    language: ReportLanguage


@dataclass(frozen=True)
class ModuleResult:
    text: str
    alerts: tuple[str, ...] = ()
    unavailable_sources: tuple[SourceCode, ...] = ()
