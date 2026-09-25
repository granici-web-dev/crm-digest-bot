from dataclasses import dataclass
from datetime import date

import pandas as pd

from digest.config import AppConfig
from digest.metrics.kpi import Period
from digest.reports.periods import ReportLevel
from digest.reports.render import ReportLanguage


@dataclass(frozen=True)
class ReportContext:
    level: ReportLevel
    period: Period
    report_date: date
    previous_frame: pd.DataFrame | None
    config: AppConfig
    language: ReportLanguage


@dataclass(frozen=True)
class ModuleResult:
    text: str
    photo: bytes | None = None
    document: bytes | None = None
