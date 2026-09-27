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
from digest.metrics.kpi import Period, count_flags, lead_counts_by_showroom

EARLIEST_TIMESTAMP = pd.Timestamp.min.tz_localize("UTC")


@dataclass(frozen=True)
class UntouchedGroup:
    # None: лид не взят (консультант с not_taken или без консультанта).
    manager_name: str | None
    lead_count: int
    oldest_age_hours: int
    # Самые старые первыми, как и во всех lead_ids ниже.
    lead_ids: tuple[int, ...]


@dataclass(frozen=True)
class UntouchedLeads:
    lead_count: int
    # None: лидов в блоке нет.
    oldest_age_hours: int | None
    groups: tuple[UntouchedGroup, ...]
    lead_ids: tuple[int, ...]


@dataclass(frozen=True)
class OverdueGroup:
    # None: лид не взят (консультант с not_taken или без консультанта).
    manager_name: str | None
    lead_count: int
    max_days_overdue: int
    lead_ids: tuple[int, ...]


@dataclass(frozen=True)
class OverdueRevenire:
    lead_count: int
    # None: просроченных revenire нет.
    max_days_overdue: int | None
    groups: tuple[OverdueGroup, ...]
    lead_ids: tuple[int, ...]


@dataclass(frozen=True)
class StaleOffers:
    by_showroom: dict[str | None, int]
    total: int
    change_since_yesterday: int | None
    lead_ids_by_showroom: dict[str | None, tuple[int, ...]]
    lead_ids: tuple[int, ...]


@dataclass(frozen=True)
class IrelevantSpike:
    manager_name: str | None
    lead_count: int


@dataclass(frozen=True)
class Anomalies:
    site_average_days: int
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


def oldest_first(leads: pd.DataFrame, age_order: pd.Series) -> pd.DataFrame:
    # Порядок ссылок на лиды: самый старый первым, при равенстве по id (детерминированно).
    return (
        leads.assign(age_order=age_order)
        .sort_values(["age_order", "lead_id"], kind="stable")
        .drop(columns="age_order")
    )


def lead_id_tuple(lead_ids: pd.Series) -> tuple[int, ...]:
    return tuple(int(lead_id) for lead_id in lead_ids)


def count_and_max_by_manager(
    leads: pd.DataFrame, values: pd.Series, config: AppConfig
) -> list[tuple[str | None, int, int, tuple[int, ...]]]:
    # leads уже упорядочены oldest_first: groupby сохраняет порядок строк внутри группы.
    grouped = (
        pd.DataFrame(
            {
                "manager_name": manager_names(leads, config),
                "value": values,
                "lead_id": leads["lead_id"],
            }
        )
        .groupby("manager_name", dropna=False, sort=False)
        .agg(size=("value", "size"), max=("value", "max"), lead_ids=("lead_id", lead_id_tuple))
    )
    groups = [
        (name_or_not_taken(manager_name), int(row["size"]), int(row["max"]), row["lead_ids"])
        for manager_name, row in grouped.iterrows()
    ]
    return sorted(groups, key=lambda group: not_taken_first(group[0], group[1]))


def untouched_leads(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> UntouchedLeads:
    # docs/kpi-definitions.md, «Ежедневные проверки», d2.
    params = config.modules.untouched_leads_params
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
    # «Contactat astăzi» включён по умолчанию: при создании last_contact_at = created_at с
    # отставанием до минуты, это не касание (docs/mefi-api-notes.md, «Наблюдения о качестве
    # данных»). Пустой или раньше создания last_contact_at тоже не касание: NaN в сравнении
    # даёт False. Лид, созданный консультантом руками, уже обработан им.
    contact_after_creation = (lead_frame["last_contact_at"] - created_at).dt.total_seconds()
    touched = (
        lead_frame["status_changed_at"].notna()
        | contact_after_creation.gt(params.touch_tolerance_seconds)
        | lead_frame["is_ofertat"]
        | lead_frame["created_by_id"].isin(consultant_ids)
    )
    not_taken = manager_names(lead_frame, config).isna()
    reported = candidate & (not_taken | ~touched)
    leads = oldest_first(lead_frame[reported], created_at[reported])
    ages = age_hours[leads.index].floordiv(1)
    groups = tuple(
        UntouchedGroup(*group) for group in count_and_max_by_manager(leads, ages, config)
    )
    return UntouchedLeads(
        len(leads),
        None if leads.empty else int(ages.max()),
        groups,
        lead_id_tuple(leads["lead_id"]),
    )


def overdue_revenire_by_manager(
    lead_frame: pd.DataFrame, today: date, config: AppConfig
) -> OverdueRevenire:
    # docs/kpi-definitions.md, «Дополнительные метрики», просроченные revenire.
    overdue_ids = overdue_revenire(lead_frame, today, config)
    overdue = lead_frame[lead_frame["lead_id"].isin(overdue_ids)]
    overdue = oldest_first(overdue, overdue["data_revenire"])
    days_overdue = (pd.Timestamp(today) - overdue["data_revenire"]).dt.days
    groups = tuple(
        OverdueGroup(*group) for group in count_and_max_by_manager(overdue, days_overdue, config)
    )
    return OverdueRevenire(
        len(overdue),
        None if overdue.empty else int(days_overdue.max()),
        groups,
        lead_id_tuple(overdue["lead_id"]),
    )


def snapshot_period(analysis_date: date, config: AppConfig) -> Period:
    # Все лиды снапшота, а не лиды периода. Начало не от created_at.min(): у пустого кадра это NaT.
    return Period(EARLIEST_TIMESTAMP, daily_window(analysis_date, config.status_mapping.time).end)


def stale_offer_counts_by_showroom(
    lead_frame: pd.DataFrame, analysis_date: date, config: AppConfig
) -> dict[str | None, int]:
    # Та же ACTIVE_OFFERS_14, что в ACR (docs/kpi-definitions.md, «Базовые множества»), но по
    # всем лидам снапшота.
    all_time = snapshot_period(analysis_date, config)
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
    flags = count_flags(lead_frame, snapshot_period(report_date, config), report_date, config)
    stale = lead_frame.loc[flags.index[flags["active_offers_14"]]]
    stale = oldest_first(stale, stale["last_contact_at"])
    showroom = stale["showroom"]
    lead_ids_by_showroom = {
        key: lead_id_tuple(
            stale.loc[showroom.isna() if key is None else showroom.eq(key), "lead_id"]
        )
        for key in by_showroom
    }
    return StaleOffers(
        by_showroom,
        total,
        change_since_yesterday,
        lead_ids_by_showroom,
        lead_id_tuple(stale["lead_id"]),
    )


def anomalies(lead_frame: pd.DataFrame, report_date: date, config: AppConfig) -> Anomalies:
    # docs/kpi-definitions.md, «Ежедневные проверки», d5.
    params = config.modules.anomaly_params
    time_settings = config.status_mapping.time
    is_site = lead_frame["source_name"].isin(config.status_mapping.sources.site)

    def site_leads(day: date) -> int:
        # Лиды считаются как строка web в d1, из них только источники sources.site.
        flags = lead_row_flags(lead_frame, daily_window(day, time_settings), config)
        return int((flags["leads_web"] & is_site).sum())

    # Среднее по created_at сегодняшнего кадра: пропуск снапшота в прошлом его не ломает.
    average_days = params.site_average_days
    previous_days_average = (
        sum(
            site_leads(report_date - timedelta(days=days_back))
            for days_back in range(1, average_days + 1)
        )
        / average_days
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
    spikes = [
        IrelevantSpike(name_or_not_taken(manager_name), int(lead_count))
        for manager_name, lead_count in counts.items()
        if lead_count >= params.irelevant_spike_min
    ]
    irelevant_spikes = tuple(
        sorted(spikes, key=lambda spike: not_taken_first(spike.manager_name, spike.lead_count))
    )
    return Anomalies(average_days, site_zero_previous_average, irelevant_spikes)


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
