from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import pandas as pd

from digest.config import AppConfig
from digest.metrics.daily import (
    LEAD_ROWS,
    PreviousSnapshot,
    comparable_previous,
    daily_window,
    lead_row_flags,
)
from digest.metrics.extra import overdue_revenire, value_difference
from digest.metrics.frame import status_set_at
from digest.metrics.kpi import (
    Period,
    converted_in_period,
    count_flags,
    lead_counts,
    lead_counts_by_showroom,
    ratio,
)

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

    def of_manager(self, manager_name: str) -> "OverdueRevenire":
        # Консультант без просроченных это ноль по построению, а не отсутствие группы.
        groups = tuple(group for group in self.groups if group.manager_name == manager_name)
        return OverdueRevenire(
            sum(group.lead_count for group in groups),
            max(group.max_days_overdue for group in groups) if groups else None,
            groups,
            tuple(lead_id for group in groups for lead_id in group.lead_ids),
        )


@dataclass(frozen=True)
class MissingFollowupGroup:
    # None: лид не взят (консультант с not_taken или без консультанта).
    manager_name: str | None
    lead_count: int
    lead_ids: tuple[int, ...]


@dataclass(frozen=True)
class MissingFollowupDate:
    lead_count: int
    groups: tuple[MissingFollowupGroup, ...]
    lead_ids: tuple[int, ...]
    # Лиды охвата, у которых поле Data revenire не прочитано (нет поля, другое имя, не дата):
    # пустым оно не считается, в блок они не входят.
    unreadable_count: int
    # Поле не прочитано ни у одного лида охвата: блок не считается вовсе.
    field_unavailable: bool

    def of_manager(self, manager_name: str) -> "MissingFollowupDate":
        groups = tuple(group for group in self.groups if group.manager_name == manager_name)
        return MissingFollowupDate(
            sum(group.lead_count for group in groups),
            groups,
            tuple(lead_id for group in groups for lead_id in group.lead_ids),
            self.unreadable_count,
            self.field_unavailable,
        )


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


TrendDirection = Literal["up", "down", "flat"]


@dataclass(frozen=True)
class RollingContractRate:
    window_days: int
    contracts: int
    useful: int
    rate: float | None
    previous_contracts: int
    previous_useful: int
    previous_rate: float | None
    difference_pp: float | None
    direction: TrendDirection | None
    trend_threshold_pp: float


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
    # Группа это консультант по id, а не по имени: тёзки не сливаются, консультант вне
    # managers.yaml получает свою строку. Имя берётся из лида. leads уже упорядочены
    # oldest_first: groupby сохраняет порядок строк внутри группы.
    manager_name = manager_names(leads, config)
    grouped = (
        pd.DataFrame(
            {
                "manager_id": leads["assigned_to_id"].where(manager_name.notna()),
                "manager_name": manager_name,
                "value": values,
                "lead_id": leads["lead_id"],
            }
        )
        .groupby("manager_id", dropna=False, sort=False)
        .agg(
            manager_name=("manager_name", "first"),
            size=("value", "size"),
            max=("value", "max"),
            lead_ids=("lead_id", lead_id_tuple),
        )
    )
    groups = [
        (
            name_or_not_taken(row["manager_name"]),
            int(row["size"]),
            int(row["max"]),
            row["lead_ids"],
        )
        for _, row in grouped.iterrows()
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
        # Приход в шоурум сам по себе касание, в том числе revenire.
        & ~lead_frame["is_showroom_source"]
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


def missing_followup_date(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> MissingFollowupDate:
    # docs/kpi-definitions.md, «Ежедневные проверки», d3 без Data revenire.
    status_mapping = config.status_mapping
    created_from = datetime.combine(
        status_mapping.leads_created_from, time(), tzinfo=ZoneInfo(status_mapping.time.timezone)
    )
    # Возраст от конца окна, как в d2.
    window_end = daily_window(report_date, status_mapping.time).end
    status_since = status_set_at(lead_frame)
    status_age_hours = (window_end - status_since).dt.total_seconds() / 3600
    min_age_hours = config.modules.overdue_revenire_params.missing_followup_min_age_hours
    in_scope = (
        lead_frame["is_followup_status"]
        & lead_frame["created_at"].ge(created_from)
        & status_age_hours.gt(min_age_hours)
    )
    unreadable = in_scope & lead_frame["data_revenire_problem"].notna()
    unreadable_count = int(unreadable.sum())
    # Поле пропало или переименовано в mefi у всех: «nu» в блоке было бы неверной цифрой.
    field_unavailable = unreadable_count > 0 and unreadable_count == int(in_scope.sum())
    reported = in_scope & ~unreadable & lead_frame["data_revenire"].isna()
    leads = oldest_first(lead_frame[reported], status_since[reported])
    groups = tuple(
        MissingFollowupGroup(manager_name, lead_count, lead_ids)
        for manager_name, lead_count, _, lead_ids in count_and_max_by_manager(
            leads, status_age_hours[leads.index], config
        )
    )
    return MissingFollowupDate(
        len(leads),
        groups,
        lead_id_tuple(leads["lead_id"]),
        unreadable_count,
        field_unavailable,
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
    marked_at = status_set_at(lead_frame)
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
        return leads, len(converted_in_period(lead_frame, window))

    leads, contracts = leads_and_contracts(report_date)
    leads_week_ago, contracts_week_ago = leads_and_contracts(week_ago_date)
    return SameWeekdayComparison(
        week_ago_date, leads, leads_week_ago, contracts, contracts_week_ago
    )


def trend_direction(difference_pp: float | None, threshold_pp: float) -> TrendDirection | None:
    if difference_pp is None:
        return None
    if abs(difference_pp) < threshold_pp:
        return "flat"
    return "up" if difference_pp > 0 else "down"


def shown_percent(share: float | None) -> float | None:
    # Как percent1 в шаблоне: f"{:.1f}" и round(, 1) округляют одно и то же двоичное значение.
    return None if share is None else round(share * 100, 1)


def rolling_contract_rate(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> RollingContractRate:
    # docs/kpi-definitions.md, «Ежедневные проверки», d7: не когорта, а темп. Договоры по
    # converted_at и USEFUL по created_at за одни и те же ежедневные окна, оба окна по одному кадру.
    params = config.modules.rolling_contract_rate_params
    time_settings = config.status_mapping.time
    window_days = params.window_days

    def contracts_and_useful(last_day: date) -> tuple[int, int]:
        window = Period(
            daily_window(last_day - timedelta(days=window_days - 1), time_settings).start,
            daily_window(last_day, time_settings).end,
        )
        useful = lead_counts(lead_frame, window, report_date, config).useful
        return len(converted_in_period(lead_frame, window)), useful

    contracts, useful = contracts_and_useful(report_date)
    previous_contracts, previous_useful = contracts_and_useful(
        report_date - timedelta(days=window_days)
    )
    rate = ratio(contracts, useful)
    previous_rate = ratio(previous_contracts, previous_useful)
    # Разница из показанных процентов: 9,96 % и 8,0 % в строке это 10,0 % и 8,0 %, и «=» при
    # пороге 2,0 противоречил бы цифрам. Внешнее округление до 1e-9: 0,3 − 2,3 в float это
    # −1,9999999999999998.
    difference = value_difference(shown_percent(rate), shown_percent(previous_rate))
    difference_pp = None if difference is None else round(difference, 9)
    return RollingContractRate(
        window_days,
        contracts,
        useful,
        rate,
        previous_contracts,
        previous_useful,
        previous_rate,
        difference_pp,
        trend_direction(difference_pp, params.trend_threshold_pp),
        params.trend_threshold_pp,
    )
