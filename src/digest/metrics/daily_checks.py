from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from digest.config import AppConfig
from digest.metrics.daily import (
    LEAD_ROWS,
    PreviousSnapshot,
    comparable_previous,
    daily_window,
    lead_row_flags,
)
from digest.metrics.extra import overdue_revenire
from digest.metrics.kpi import Period, lead_counts_by_showroom


@dataclass(frozen=True)
class UntouchedGroup:
    # None: лид не взят (консультант с not_taken или без консультанта).
    manager_name: str | None
    lead_count: int
    oldest_age_hours: int


@dataclass(frozen=True)
class UntouchedLeads:
    lead_count: int
    # None: лидов в блоке нет.
    oldest_age_hours: int | None
    groups: tuple[UntouchedGroup, ...]


@dataclass(frozen=True)
class OverdueGroup:
    # None: лид не взят (консультант с not_taken или без консультанта).
    manager_name: str | None
    lead_count: int
    max_days_overdue: int


@dataclass(frozen=True)
class OverdueRevenire:
    lead_count: int
    # None: просроченных revenire нет.
    max_days_overdue: int | None
    groups: tuple[OverdueGroup, ...]


@dataclass(frozen=True)
class StaleOffers:
    by_showroom: dict[str | None, int]
    total: int
    change_since_yesterday: int | None


@dataclass(frozen=True)
class IrelevantSpike:
    manager_name: str | None
    lead_count: int


@dataclass(frozen=True)
class Anomalies:
    # None: правило «сайт молчит» не сработало.
    site_zero_previous_average: float | None
    irelevant_spikes: tuple[IrelevantSpike, ...]


@dataclass(frozen=True)
class SameWeekdayComparison:
    week_ago_date: date
    leads: int
    leads_week_ago: int
    contracts: int
    contracts_week_ago: int


def manager_names(leads: pd.DataFrame, config: AppConfig) -> pd.Series:
    # Лид на Marketing Sofa (Desemnat по умолчанию в mefi) или без консультанта никто не взял.
    assigned_to_id = leads["assigned_to_id"]
    not_taken = assigned_to_id.isna() | assigned_to_id.isin(config.managers.not_taken_ids)
    return leads["assigned_to_name"].where(~not_taken)


def name_or_not_taken(group_key: object) -> str | None:
    return group_key if isinstance(group_key, str) else None


def not_taken_first(manager_name: str | None, lead_count: int) -> tuple[bool, int, str]:
    return (manager_name is not None, -lead_count, manager_name or "")


def count_and_max_by_manager(
    leads: pd.DataFrame, values: pd.Series, config: AppConfig
) -> list[tuple[str | None, int, int]]:
    grouped = (
        pd.DataFrame({"manager_name": manager_names(leads, config), "value": values})
        .groupby("manager_name", dropna=False)["value"]
        .agg(["size", "max"])
    )
    groups = [
        (name_or_not_taken(manager_name), int(row["size"]), int(row["max"]))
        for manager_name, row in grouped.iterrows()
    ]
    return sorted(groups, key=lambda group: not_taken_first(group[0], group[1]))


def untouched_leads(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> UntouchedLeads:
    # docs/kpi-definitions.md, «Ежедневные проверки», d2.
    params = config.modules.untouched_leads_params()
    # Возраст от конца окна, а не от now(): повтор отчёта даёт те же цифры.
    window_end = daily_window(report_date, config.status_mapping.time).end
    created_at = lead_frame["created_at"]
    age_hours = (window_end - created_at).dt.total_seconds() / 3600
    candidate = (
        created_at.ge(window_end - timedelta(days=params.lookback_days))
        & created_at.lt(window_end)
        & age_hours.gt(params.threshold_hours)
        & ~lead_frame["is_excluded_from_leads"]
        # Визит в шоуруме сам по себе касание.
        & ~lead_frame["is_showroom_visit"]
    )
    consultant_ids = [manager.id for manager in config.managers.managers if manager.active]
    # «Contactat astăzi» включён по умолчанию: при создании last_contact_at = created_at, это
    # не касание (docs/mefi-api-notes.md, «Наблюдения о качестве данных»). Лид, созданный
    # консультантом руками, уже обработан им.
    touched = (
        lead_frame["status_changed_at"].notna()
        | lead_frame["last_contact_at"].ne(created_at)
        | lead_frame["is_ofertat"]
        | lead_frame["created_by_id"].isin(consultant_ids)
    )
    not_taken = manager_names(lead_frame, config).isna()
    reported = candidate & (not_taken | ~touched)
    leads = lead_frame[reported]
    ages = age_hours[reported].floordiv(1)
    groups = tuple(
        UntouchedGroup(*group) for group in count_and_max_by_manager(leads, ages, config)
    )
    return UntouchedLeads(len(leads), None if leads.empty else int(ages.max()), groups)


def overdue_revenire_by_manager(
    lead_frame: pd.DataFrame, today: date, config: AppConfig
) -> OverdueRevenire:
    # docs/kpi-definitions.md, «Дополнительные метрики», просроченные revenire.
    overdue = lead_frame[lead_frame["lead_id"].isin(overdue_revenire(lead_frame, today, config))]
    days_overdue = (pd.Timestamp(today) - overdue["data_revenire"]).dt.days
    groups = tuple(
        OverdueGroup(*group) for group in count_and_max_by_manager(overdue, days_overdue, config)
    )
    return OverdueRevenire(len(overdue), None if overdue.empty else int(days_overdue.max()), groups)


def stale_offer_counts_by_showroom(
    lead_frame: pd.DataFrame, analysis_date: date, config: AppConfig
) -> dict[str | None, int]:
    # Та же ACTIVE_OFFERS_14, что в ACR (docs/kpi-definitions.md, «Базовые множества»), но по
    # всем лидам снапшота, а не по лидам периода.
    all_time = Period(
        lead_frame["created_at"].min(),
        daily_window(analysis_date, config.status_mapping.time).end,
    )
    counts = lead_counts_by_showroom(lead_frame, all_time, analysis_date, config)
    return {
        showroom: showroom_counts.active_offers_14 for showroom, showroom_counts in counts.items()
    }


def stale_offers(
    lead_frame: pd.DataFrame,
    previous: PreviousSnapshot | None,
    report_date: date,
    config: AppConfig,
) -> StaleOffers:
    by_showroom = stale_offer_counts_by_showroom(lead_frame, report_date, config)
    total = sum(by_showroom.values())
    comparable = comparable_previous(previous, report_date)
    change_since_yesterday = (
        None
        if comparable is None
        else total
        - sum(
            stale_offer_counts_by_showroom(
                comparable.frame, comparable.snapshot_date, config
            ).values()
        )
    )
    return StaleOffers(by_showroom, total, change_since_yesterday)


def anomalies(lead_frame: pd.DataFrame, report_date: date, config: AppConfig) -> Anomalies:
    # docs/kpi-definitions.md, «Ежедневные проверки», d5.
    params = config.modules.anomaly_params()
    time_settings = config.status_mapping.time
    is_site = lead_frame["source_name"].isin(config.status_mapping.sources.site)

    def site_leads(day: date) -> int:
        # Лиды считаются как строка web в d1, из них только источники sources.site.
        flags = lead_row_flags(lead_frame, daily_window(day, time_settings), config)
        return int((flags["leads_web"] & is_site).sum())

    # Среднее по created_at сегодняшнего кадра: пропуск снапшота в прошлом его не ломает.
    previous_days_average = (
        sum(site_leads(report_date - timedelta(days=days_back)) for days_back in range(1, 8)) / 7
    )
    site_zero_previous_average = (
        previous_days_average
        if site_leads(report_date) == 0 and previous_days_average >= params.site_zero_min_average
        else None
    )

    window = daily_window(report_date, time_settings)
    # status_changed_at = null, если статус задан при создании (CLAUDE.md): тогда момент
    # отметки IRELEVANT это created_at.
    marked_at = lead_frame["status_changed_at"].fillna(lead_frame["created_at"])
    marked_in_window = (
        lead_frame["is_irelevant"] & marked_at.ge(window.start) & marked_at.lt(window.end)
    )
    counts = manager_names(lead_frame[marked_in_window], config).value_counts(dropna=False)
    irelevant_spikes = tuple(
        IrelevantSpike(name_or_not_taken(manager_name), int(lead_count))
        for manager_name, lead_count in counts.items()
        if lead_count >= params.irelevant_spike_min
    )
    return Anomalies(site_zero_previous_average, irelevant_spikes)


def same_weekday_comparison(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> SameWeekdayComparison:
    # docs/kpi-definitions.md, «Ежедневные проверки», d6: оба окна из сегодняшнего кадра.
    time_settings = config.status_mapping.time
    week_ago_date = report_date - timedelta(days=7)

    def leads_and_contracts(day: date) -> tuple[int, int]:
        window = daily_window(day, time_settings)
        leads = int(lead_row_flags(lead_frame, window, config)[list(LEAD_ROWS)].to_numpy().sum())
        converted_at = lead_frame["converted_at"]
        contracts = int((converted_at.ge(window.start) & converted_at.lt(window.end)).sum())
        return leads, contracts

    leads, contracts = leads_and_contracts(report_date)
    leads_week_ago, contracts_week_ago = leads_and_contracts(week_ago_date)
    return SameWeekdayComparison(
        week_ago_date, leads, leads_week_ago, contracts, contracts_week_ago
    )
