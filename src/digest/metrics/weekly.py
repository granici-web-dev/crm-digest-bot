from collections import Counter
from dataclasses import dataclass
from datetime import date, time, timedelta

import pandas as pd

from digest.config import AppConfig, TimeSettings
from digest.metrics.daily import PreviousSnapshot, daily_window, transition_flags
from digest.metrics.kpi import Kpis, LeadCounts, Period, kpis_from, lead_counts, ratio

DAYS_IN_WEEK = 7
# Лист «Lead-uri» и «Vizite» в Excel: только эти колонки, без имени, телефона и e-mail клиента
# (инвариант 7). Консультант это сотрудник, его имя выводить можно.
LEAD_ROW_COLUMNS = (
    "lead_id",
    "created_at",
    "day",
    "showroom",
    "source_name",
    "status_name",
    "ofertat",
    "consultant",
)


@dataclass(frozen=True)
class DayShowroomCounts:
    days: tuple[date, ...]
    showrooms: tuple[str | None, ...]
    counts: dict[date, dict[str | None, int]]

    def day_total(self, day: date) -> int:
        return sum(self.counts[day].values())

    def showroom_total(self, showroom: str | None) -> int:
        return sum(by_showroom[showroom] for by_showroom in self.counts.values())

    @property
    def total(self) -> int:
        return sum(self.day_total(day) for day in self.days)


@dataclass(frozen=True)
class WeeklyLeadTables:
    by_day_showroom: DayShowroomCounts
    sources: tuple[str | None, ...]
    by_showroom_source: dict[str | None, dict[str | None, int]]
    by_day_showroom_source: dict[date, dict[str | None, dict[str | None, int]]]

    def source_total(self, source: str | None) -> int:
        return sum(by_source[source] for by_source in self.by_showroom_source.values())

    @property
    def total(self) -> int:
        return self.by_day_showroom.total


@dataclass(frozen=True)
class WeeklyFunnel:
    counts: LeadCounts
    kpis: Kpis


@dataclass(frozen=True)
class LossReasons:
    reasons: tuple[str, ...]
    by_showroom: dict[str | None, dict[str, int]]

    def reason_total(self, reason: str) -> int:
        return sum(by_reason[reason] for by_reason in self.by_showroom.values())

    @property
    def total(self) -> int:
        return sum(self.reason_total(reason) for reason in self.reasons)


@dataclass(frozen=True)
class WeekOverWeek:
    leads: int
    leads_previous: int
    showroom_visits: int
    showroom_visits_previous: int
    contracts: int
    contracts_previous: int
    offers: int | None


def week_days(report_date: date) -> tuple[date, ...]:
    return tuple(report_date - timedelta(days=offset) for offset in range(DAYS_IN_WEEK - 1, -1, -1))


def weekly_window(report_date: date, time_settings: TimeSettings) -> Period:
    # Окно недели = семь ежедневных окон d1 подряд (docs/kpi-definitions.md, «Недельные окна»):
    # визиты, контракты и потери недели равны сумме ежедневных, между неделями ничего не теряется.
    first_day = report_date - timedelta(days=DAYS_IN_WEEK - 1)
    return Period(
        daily_window(first_day, time_settings).start, daily_window(report_date, time_settings).end
    )


def shifted_days(created_at: pd.Series, same_day: pd.Series) -> pd.Series:
    # tz_localize(None) оставляет время по Бухаресту: день берётся по местным часам, в том числе
    # в день перевода часов.
    local_day = created_at.dt.tz_localize(None).dt.normalize()
    days: pd.Series = (local_day + pd.to_timedelta((~same_day).astype(int), unit="D")).dt.date
    return days


def working_days(created_at: pd.Series, time_settings: TimeSettings) -> pd.Series:
    # Правило ручного понедельничного отчёта (docs/kpi-definitions.md, «Недельные окна»): лид в
    # [10:00, 19:00) по Бухаресту идёт в свой день, любой другой, включая утро до 10:00,
    # в следующий календарный день.
    hours = time_settings.working_hours
    local_time = created_at.dt.time
    return shifted_days(created_at, local_time.ge(hours.start) & local_time.lt(hours.end))


def daily_window_days(created_at: pd.Series, time_settings: TimeSettings) -> pd.Series:
    window_end: time = time_settings.daily_window_end
    return shifted_days(created_at, created_at.dt.time.lt(window_end))


def weekly_leads(lead_frame: pd.DataFrame, report_date: date, config: AppConfig) -> pd.DataFrame:
    # Источник Showroom в недельном счёте лидов не участвует (ручной отчёт «FARA Showroom»),
    # визиты считает weekly_showroom_visit_leads.
    days = working_days(lead_frame["created_at"], config.status_mapping.time)
    in_week = days.isin(week_days(report_date)) & ~lead_frame["is_showroom_visit"]
    return lead_frame[in_week].assign(day=days[in_week])


def weekly_showroom_visit_leads(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> pd.DataFrame:
    time_settings = config.status_mapping.time
    window = weekly_window(report_date, time_settings)
    created_at = lead_frame["created_at"]
    in_window = created_at.ge(window.start) & created_at.lt(window.end)
    visits = lead_frame[in_window & lead_frame["is_showroom_visit"]]
    return visits.assign(day=daily_window_days(visits["created_at"], time_settings))


def key_or_none(value: object) -> str | None:
    return None if pd.isna(value) else str(value)  # type: ignore[call-overload]


def showroom_keys(showroom: pd.Series, config: AppConfig) -> tuple[str | None, ...]:
    # Шоурум вне списка showrooms получает свою колонку: лиды не пропадают молча (как в kpi.py).
    configured = config.status_mapping.showrooms
    observed = sorted(set(showroom.dropna()) - set(configured))
    return (*configured, *observed, None)


def source_keys(source: pd.Series) -> tuple[str | None, ...]:
    # Порядок колонок как в ручных отчётах июля: по убыванию итога недели, при равенстве по
    # алфавиту, «(fără sursă)» последней.
    totals = Counter(source.dropna())
    return (*sorted(totals, key=lambda name: (-totals[name], name)), None)


def day_showroom_counts(
    leads: pd.DataFrame, report_date: date, config: AppConfig
) -> DayShowroomCounts:
    showrooms = showroom_keys(leads["showroom"], config)
    pairs = Counter(zip(leads["day"], map(key_or_none, leads["showroom"]), strict=True))
    days = week_days(report_date)
    counts = {day: {showroom: pairs[day, showroom] for showroom in showrooms} for day in days}
    return DayShowroomCounts(days, showrooms, counts)


def weekly_lead_tables(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> WeeklyLeadTables:
    leads = weekly_leads(lead_frame, report_date, config)
    by_day_showroom = day_showroom_counts(leads, report_date, config)
    sources = source_keys(leads["source_name"])
    triples = Counter(
        zip(
            leads["day"],
            map(key_or_none, leads["showroom"]),
            map(key_or_none, leads["source_name"]),
            strict=True,
        )
    )
    by_showroom_source = {
        showroom: {
            source: sum(triples[day, showroom, source] for day in by_day_showroom.days)
            for source in sources
        }
        for showroom in by_day_showroom.showrooms
    }
    by_day_showroom_source = {
        day: {
            showroom: {source: triples[day, showroom, source] for source in sources}
            for showroom in by_day_showroom.showrooms
            if by_day_showroom.counts[day][showroom]
        }
        for day in by_day_showroom.days
    }
    return WeeklyLeadTables(by_day_showroom, sources, by_showroom_source, by_day_showroom_source)


def weekly_showroom_visits(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> DayShowroomCounts:
    visits = weekly_showroom_visit_leads(lead_frame, report_date, config)
    return day_showroom_counts(visits, report_date, config)


def weekly_funnel(lead_frame: pd.DataFrame, report_date: date, config: AppConfig) -> WeeklyFunnel:
    # Когорта лидов, созданных в окне недели, на дату снапшота: оферты и контракты по ней ещё
    # будут расти (docs/kpi-definitions.md, «Недельные окна»).
    window = weekly_window(report_date, config.status_mapping.time)
    counts = lead_counts(lead_frame, window, report_date, config)
    return WeeklyFunnel(counts, kpis_from(counts))


def weekly_loss_reasons(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> LossReasons:
    # Потеря недели: статус сменился в окне; лид, созданный сразу со статусом потери, имеет
    # status_changed_at = null (CLAUDE.md, ловушки mefi) и считается по created_at.
    window = weekly_window(report_date, config.status_mapping.time)
    changed_at, created_at = lead_frame["status_changed_at"], lead_frame["created_at"]
    changed_in_window = changed_at.ge(window.start) & changed_at.lt(window.end)
    created_lost_in_window = (
        changed_at.isna() & created_at.ge(window.start) & created_at.lt(window.end)
    )
    lost = lead_frame[
        lead_frame["category"].eq("LOST") & (changed_in_window | created_lost_in_window)
    ]
    reasons = tuple(config.status_mapping.categories.LOST.reasons)
    pairs = Counter(zip(map(key_or_none, lost["showroom"]), lost["loss_reason"], strict=True))
    by_showroom = {
        showroom: {reason: pairs[showroom, reason] for reason in reasons}
        for showroom in showroom_keys(lost["showroom"], config)
    }
    return LossReasons(reasons, by_showroom)


def converted_count(lead_frame: pd.DataFrame, window: Period) -> int:
    converted_at = lead_frame["converted_at"]
    return int((converted_at.ge(window.start) & converted_at.lt(window.end)).sum())


def week_over_week(
    lead_frame: pd.DataFrame,
    week_ago: PreviousSnapshot | None,
    report_date: date,
    config: AppConfig,
) -> WeekOverWeek:
    # Обе недели по одному воскресному снапшоту: created_at и converted_at не меняются.
    # Оферты видны только как разница снапшотов (инвариант 3), и только с ровно недельным:
    # более старый снапшот покрыл бы больше недели.
    time_settings = config.status_mapping.time
    previous_date = report_date - timedelta(days=DAYS_IN_WEEK)
    window = weekly_window(report_date, time_settings)
    offers = None
    if week_ago is not None and week_ago.snapshot_date == previous_date:
        offers = int(transition_flags(lead_frame, week_ago.frame, window, config)["offers"].sum())
    return WeekOverWeek(
        leads=len(weekly_leads(lead_frame, report_date, config)),
        leads_previous=len(weekly_leads(lead_frame, previous_date, config)),
        showroom_visits=len(weekly_showroom_visit_leads(lead_frame, report_date, config)),
        showroom_visits_previous=len(
            weekly_showroom_visit_leads(lead_frame, previous_date, config)
        ),
        contracts=converted_count(lead_frame, window),
        contracts_previous=converted_count(lead_frame, weekly_window(previous_date, time_settings)),
        offers=offers,
    )


def relative_change(current: int, previous: int) -> float | None:
    return ratio(current - previous, previous)


def excel_rows(leads: pd.DataFrame, config: AppConfig) -> pd.DataFrame:
    known_manager_ids = {manager.id for manager in config.managers.managers}
    ofertat_field = config.status_mapping.custom_fields.ofertat
    assigned_to_id = leads["assigned_to_id"]
    consultant = leads["assigned_to_name"].where(
        assigned_to_id.isin(known_manager_ids), "id " + assigned_to_id.astype("string")
    )
    ofertat = leads["ofertat"].map(
        {True: ofertat_field.ofertat_yes, False: ofertat_field.ofertat_no}
    )
    rows = leads.assign(
        created_at=leads["created_at"].dt.tz_localize(None),
        consultant=consultant,
        ofertat=ofertat,
    )
    return rows.sort_values(["day", "created_at"])[list(LEAD_ROW_COLUMNS)].reset_index(drop=True)


def weekly_lead_rows(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> pd.DataFrame:
    return excel_rows(weekly_leads(lead_frame, report_date, config), config)


def weekly_showroom_visit_rows(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> pd.DataFrame:
    return excel_rows(weekly_showroom_visit_leads(lead_frame, report_date, config), config)
