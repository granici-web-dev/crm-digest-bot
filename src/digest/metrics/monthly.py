from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from digest.config import AppConfig, TimeSettings
from digest.metrics.cockpit import meets_target
from digest.metrics.daily import daily_window
from digest.metrics.kpi import (
    LeadCounts,
    Period,
    kpis_from,
    lead_counts,
    lead_counts_by_showroom,
    leads_in_period,
    ratio,
)
from digest.metrics.weekly import (
    LossReasons,
    converted_count,
    lead_rows,
    loss_reasons_in_window,
    relative_change,
)

TREND_MONTHS = 6
BELOW_ALL_LEVELS = "below_levels"


@dataclass(frozen=True)
class MonthlyFunnel:
    month: date
    company: LeadCounts
    by_showroom: dict[str | None, LeadCounts]

    @property
    def showrooms_with_leads(self) -> tuple[str | None, ...]:
        return tuple(showroom for showroom, counts in self.by_showroom.items() if counts.leads)

    @property
    def named_showrooms_with_leads(self) -> tuple[str, ...]:
        return tuple(showroom for showroom in self.showrooms_with_leads if showroom is not None)

    @property
    def without_showroom(self) -> LeadCounts:
        # lead_counts_by_showroom всегда отдаёт ключ None последним.
        return self.by_showroom[None]


@dataclass(frozen=True)
class ScrRow:
    scr: float | None
    level: str | None
    meets_target: bool | None


@dataclass(frozen=True)
class MonthlyScr:
    company: ScrRow
    by_showroom: dict[str | None, ScrRow]


@dataclass(frozen=True)
class MonthlyTrend:
    months: tuple[date, ...]
    leads: tuple[int, ...]
    contracts: tuple[int, ...]


@dataclass(frozen=True)
class MonthlyLossReasons:
    month: date
    previous_month: date
    current: LossReasons
    previous: LossReasons

    def reason_change(self, reason: str) -> float | None:
        return relative_change(
            self.current.reason_total(reason), self.previous.reason_total(reason)
        )

    @property
    def total_change(self) -> float | None:
        return relative_change(self.current.total, self.previous.total)

    def reason_share(self, reason: str) -> float | None:
        # Доля причины = причина / все потери месяца (docs/kpi-definitions.md, «Месячное окно», m8).
        return ratio(self.current.reason_total(reason), self.current.total)

    @property
    def total_share(self) -> float | None:
        return ratio(self.current.total, self.current.total)

    @property
    def reasons_by_count(self) -> tuple[str, ...]:
        # Причина с нулём в этом месяце, но не в прошлом, остаётся в списке: её исчезновение
        # тоже динамика.
        counted = [
            reason
            for reason in self.current.reasons
            if self.current.reason_total(reason) or self.previous.reason_total(reason)
        ]
        return tuple(
            sorted(
                counted,
                key=lambda reason: (
                    -self.current.reason_total(reason),
                    -self.previous.reason_total(reason),
                ),
            )
        )


def first_day_of_month(day: date) -> date:
    return day.replace(day=1)


def last_day_of_month(day: date) -> date:
    return day.replace(day=monthrange(day.year, day.month)[1])


def month_window(report_date: date, time_settings: TimeSettings) -> Period:
    # Окно месяца = ежедневные окна всех дней месяца (docs/kpi-definitions.md, «Месячное окно»):
    # снапшот последнего дня снят в 19:00, лиды 19:00–24:00 уходят в следующий месяц, а не теряются.
    return Period(
        daily_window(first_day_of_month(report_date), time_settings).start,
        daily_window(last_day_of_month(report_date), time_settings).end,
    )


def trend_months(report_date: date) -> tuple[date, ...]:
    months = [first_day_of_month(report_date)]
    for _ in range(TREND_MONTHS - 1):
        months.append(first_day_of_month(months[-1] - timedelta(days=1)))
    return tuple(reversed(months))


def monthly_funnel(lead_frame: pd.DataFrame, report_date: date, config: AppConfig) -> MonthlyFunnel:
    # Когорта лидов месяца на дату снапшота, как недельная воронка w3 (docs/kpi-definitions.md,
    # «Месячное окно», m2 и m4).
    window = month_window(report_date, config.status_mapping.time)
    return MonthlyFunnel(
        first_day_of_month(report_date),
        lead_counts(lead_frame, window, report_date, config),
        lead_counts_by_showroom(lead_frame, window, report_date, config),
    )


def scr_level(scr: float | None, config: AppConfig) -> str | None:
    # Первая ступень сверху, порог включительно, как цели m5 (docs/kpi-definitions.md, «KPI»).
    if scr is None:
        return None
    for level in config.modules.scr_levels_params.levels:
        if scr >= config.kpi.thresholds[level.threshold]:
            return level.threshold
    return BELOW_ALL_LEVELS


def scr_row(counts: LeadCounts, config: AppConfig) -> ScrRow:
    scr = kpis_from(counts).scr
    target = config.kpi.targets["scr"]
    return ScrRow(
        scr,
        scr_level(scr, config),
        meets_target(scr, config.kpi.target_value("scr"), target.direction),
    )


def monthly_scr(funnel: MonthlyFunnel, config: AppConfig) -> MonthlyScr:
    return MonthlyScr(
        scr_row(funnel.company, config),
        {showroom: scr_row(counts, config) for showroom, counts in funnel.by_showroom.items()},
    )


def monthly_trend(lead_frame: pd.DataFrame, report_date: date, config: AppConfig) -> MonthlyTrend:
    # Все месяцы из одного снапшота: created_at и converted_at не меняются. Контракт месяца по
    # converted_at, не по когорте лидов (docs/kpi-definitions.md, «Месячное окно»).
    time_settings = config.status_mapping.time
    windows = [month_window(month, time_settings) for month in trend_months(report_date)]
    return MonthlyTrend(
        trend_months(report_date),
        tuple(lead_counts(lead_frame, window, report_date, config).leads for window in windows),
        tuple(converted_count(lead_frame, window) for window in windows),
    )


def monthly_loss_reasons(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> MonthlyLossReasons:
    # docs/kpi-definitions.md, «Месячное окно», m8. Прошлый месяц по тому же снапшоту: лид, у
    # которого статус с тех пор сменился ещё раз, из прошлого месяца выпадает (status_changed_at
    # хранит только последнее изменение, CLAUDE.md, «Ловушки mefi API»).
    time_settings = config.status_mapping.time
    previous_month = first_day_of_month(report_date) - timedelta(days=1)
    return MonthlyLossReasons(
        first_day_of_month(report_date),
        first_day_of_month(previous_month),
        loss_reasons_in_window(lead_frame, month_window(report_date, time_settings), config),
        loss_reasons_in_window(lead_frame, month_window(previous_month, time_settings), config),
    )


def monthly_lead_rows(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> pd.DataFrame:
    # Те же лиды, что LEADS компании в m2: строки листа сходятся с воронкой
    # (docs/kpi-definitions.md, «Месячное окно», m19).
    leads = leads_in_period(lead_frame, month_window(report_date, config.status_mapping.time))
    return lead_rows(leads.assign(day=leads["created_at"].dt.tz_localize(None).dt.date))
