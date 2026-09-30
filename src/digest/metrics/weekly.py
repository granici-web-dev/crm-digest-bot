from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from digest.config import AppConfig, TimeSettings
from digest.metrics.breakdown import BreakdownColumn, lead_counts_by_column
from digest.metrics.cockpit import meets_target
from digest.metrics.daily import (
    PreviousSnapshot,
    daily_window,
    daily_window_days,
    shifted_days,
    transition_flags,
)
from digest.metrics.kpi import (
    Kpis,
    LeadCounts,
    Period,
    add_counts,
    converted_in_period,
    kpis_from,
    lead_counts,
    ratio,
)

DAYS_IN_WEEK = 7
# Строки лидов недели для вложения: только эти колонки, без имени, телефона, e-mail и заметок
# клиента (инвариант 7). assigned_to_name это имя сотрудника, его выводить можно.
LEAD_ROW_COLUMNS = (
    "lead_id",
    "created_at",
    "day",
    "showroom",
    "source_name",
    "status_name",
    "ofertat",
    "assigned_to_id",
    "assigned_to_name",
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

    def showroom_total(self, showroom: str | None) -> int:
        return sum(self.by_showroom_source[showroom].values())

    def day_source_totals(self, day: date) -> dict[str | None, int]:
        by_showroom = self.by_day_showroom_source[day]
        return {
            source: sum(by_source[source] for by_source in by_showroom.values())
            for source in self.sources
        }

    def day_showroom_total(self, day: date, showroom: str | None) -> int:
        return self.by_day_showroom.counts[day][showroom]

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

    def showroom_total(self, showroom: str | None) -> int:
        return sum(self.by_showroom[showroom].values())

    def reason_share(self, reason: str) -> float | None:
        # docs/kpi-definitions.md, «Дополнительные метрики»: доля причины от всех потерь.
        total = self.total
        return None if total == 0 else self.reason_total(reason) / total

    def showroom_reason_share(self, showroom: str | None, reason: str) -> float | None:
        total = self.showroom_total(showroom)
        return None if total == 0 else self.by_showroom[showroom][reason] / total

    @property
    def reasons_by_count(self) -> tuple[str, ...]:
        counted = [reason for reason in self.reasons if self.reason_total(reason)]
        return tuple(sorted(counted, key=lambda reason: -self.reason_total(reason)))

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


@dataclass(frozen=True)
class IrrelevantRow:
    key: str
    leads: int
    irr_leads: int
    irr: float
    # None: снапшота прошлой недели нет или у ключа на прошлой неделе не было лидов.
    irr_previous: float | None
    meets_target: bool | None


@dataclass(frozen=True)
class WeeklyIrrelevant:
    by_campaign: tuple[IrrelevantRow, ...]
    by_source: tuple[IrrelevantRow, ...]
    total_irr: float | None
    total_irr_previous: float | None
    # Снапшот, по которому посчитана прошлая неделя; None: снапшота нет.
    previous_snapshot_date: date | None


def week_days(report_date: date) -> tuple[date, ...]:
    return tuple(report_date - timedelta(days=offset) for offset in range(DAYS_IN_WEEK - 1, -1, -1))


def weekly_window(report_date: date, time_settings: TimeSettings) -> Period:
    # Окно недели = семь ежедневных окон d1 подряд (docs/kpi-definitions.md, «Недельные окна»):
    # визиты, контракты и потери недели равны сумме ежедневных, между неделями ничего не теряется.
    first_day = report_date - timedelta(days=DAYS_IN_WEEK - 1)
    return Period(
        daily_window(first_day, time_settings).start, daily_window(report_date, time_settings).end
    )


def working_days(created_at: pd.Series, time_settings: TimeSettings) -> pd.Series:
    # Правило ручного понедельничного отчёта (docs/kpi-definitions.md, «Недельные окна»): лид в
    # [10:00, 19:00) по Бухаресту идёт в свой день, любой другой, включая утро до 10:00,
    # в следующий календарный день.
    hours = time_settings.working_hours
    local_time = created_at.dt.time
    return shifted_days(created_at, local_time.ge(hours.start) & local_time.lt(hours.end))


def weekly_leads(lead_frame: pd.DataFrame, report_date: date, config: AppConfig) -> pd.DataFrame:
    # Источник Showroom в недельном счёте лидов не участвует (ручной отчёт «FARA Showroom»),
    # ни визит, ни revenire, ни партнёр; визиты считает weekly_showroom_visit_leads.
    days = working_days(lead_frame["created_at"], config.status_mapping.time)
    in_week = days.isin(week_days(report_date)) & ~lead_frame["is_showroom_source"]
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


def weekly_showroom_revenire_count(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> int:
    window = weekly_window(report_date, config.status_mapping.time)
    created_at = lead_frame["created_at"]
    in_window = created_at.ge(window.start) & created_at.lt(window.end)
    return int((in_window & lead_frame["is_showroom_revenire"]).sum())


def key_or_none(value: str | float | None) -> str | None:
    return None if value is None or pd.isna(value) else str(value)


def showroom_keys(showroom: pd.Series, config: AppConfig) -> tuple[str | None, ...]:
    # Шоурум вне списка showrooms получает свою колонку: лиды не пропадают молча (как в kpi.py).
    configured = config.status_mapping.showrooms
    observed = sorted(set(showroom.dropna()) - set(configured))
    return (*configured, *observed, None)


def source_keys(source: pd.Series) -> tuple[str | None, ...]:
    # Порядок колонок как в ручных отчётах июля: по убыванию итога недели, при равенстве по
    # алфавиту, лиды без источника последней колонкой.
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
    return loss_reasons_in_window(
        lead_frame, weekly_window(report_date, config.status_mapping.time), config
    )


def loss_reasons_in_window(
    lead_frame: pd.DataFrame, window: Period, config: AppConfig
) -> LossReasons:
    # Потеря окна: статус сменился в окне; лид, созданный сразу со статусом потери, имеет
    # status_changed_at = null (CLAUDE.md, ловушки mefi) и считается по created_at.
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
    return len(converted_in_period(lead_frame, window))


def converted_count_by_showroom(
    lead_frame: pd.DataFrame, window: Period, config: AppConfig
) -> dict[str | None, int]:
    # docs/kpi-definitions.md, «Режим вопросов», контракты по шоуруму.
    converted = converted_in_period(lead_frame, window)
    totals = Counter(map(key_or_none, converted["showroom"]))
    return {showroom: totals[showroom] for showroom in showroom_keys(converted["showroom"], config)}


def week_over_week(
    lead_frame: pd.DataFrame,
    week_ago: PreviousSnapshot | None,
    report_date: date,
    config: AppConfig,
) -> WeekOverWeek:
    # Обе недели по одному воскресному снапшоту: created_at и converted_at не меняются. Удаление
    # лида или смена источника между воскресеньями меняет прошлую неделю (docs/kpi-definitions.md,
    # «Недельные окна»). Оферты видны только как разница снапшотов (инвариант 3); week_ago это
    # снапшот ровно за прошлое воскресенье, раннер другой не передаёт.
    time_settings = config.status_mapping.time
    previous_date = report_date - timedelta(days=DAYS_IN_WEEK)
    window = weekly_window(report_date, time_settings)
    offers = None
    if week_ago is not None:
        offers = int(transition_flags(lead_frame, week_ago.frame, window)["offers"].sum())
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
    # docs/kpi-definitions.md, «Недельные окна», w8: прошлое значение 0 даёт «—», в том числе
    # 0 против 0.
    return ratio(current - previous, previous)


def lead_rows(leads: pd.DataFrame) -> pd.DataFrame:
    return leads.sort_values(["day", "created_at"])[list(LEAD_ROW_COLUMNS)].reset_index(drop=True)


def weekly_lead_rows(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> pd.DataFrame:
    return lead_rows(weekly_leads(lead_frame, report_date, config))


def weekly_showroom_visit_rows(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> pd.DataFrame:
    return lead_rows(weekly_showroom_visit_leads(lead_frame, report_date, config))


def irrelevant_rows(
    current: dict[str | None, LeadCounts],
    previous: dict[str | None, LeadCounts] | None,
    min_leads: int,
    top_rows: int,
    config: AppConfig,
) -> tuple[IrrelevantRow, ...]:
    # Пустой источник или кампания в рейтинг не входят: по ним нечего менять, но их лиды в итоге
    # (docs/kpi-definitions.md, «Разбивка по источникам и кампаниям», w6).
    target = config.kpi.targets["irr"]
    rows = []
    for key, counts in current.items():
        if key is None or counts.leads < min_leads:
            continue
        irr = kpis_from(counts).irr
        if irr is None:
            # min_leads >= 1 и leads >= min_leads, значит leads > 0 и IRR определён; None здесь
            # означал бы сломанный порог в конфиге, а не пустую строку рейтинга.
            raise ValueError(f"IRR не определён при leads={counts.leads}, min_leads={min_leads}")
        previous_counts = None if previous is None else previous.get(key)
        rows.append(
            IrrelevantRow(
                key=key,
                leads=counts.leads,
                irr_leads=counts.irr_leads,
                irr=irr,
                irr_previous=None if previous_counts is None else kpis_from(previous_counts).irr,
                meets_target=meets_target(irr, config.kpi.target_value("irr"), target.direction),
            )
        )
    rows.sort(key=lambda row: (-row.irr, -row.irr_leads, row.key))
    return tuple(rows[:top_rows])


def weekly_irrelevant(
    lead_frame: pd.DataFrame,
    previous_week: PreviousSnapshot | None,
    report_date: date,
    config: AppConfig,
) -> WeeklyIrrelevant:
    # Та же когорта, что w3: итог IRR равен IRR воронки недели. Прошлая неделя по снапшоту её
    # воскресенья, а не по текущему: IRELEVANT ставят с задержкой, и по сегодняшнему снапшоту
    # прошлая неделя выглядела бы хуже текущей (docs/kpi-definitions.md, «Разбивка по источникам
    # и кампаниям», w6). Снапшота за воскресенье нет: раннер передаёт первый более поздний.
    time_settings = config.status_mapping.time
    params = config.modules.irr_by_campaign_params
    window = weekly_window(report_date, time_settings)
    previous_window = weekly_window(report_date - timedelta(days=DAYS_IN_WEEK), time_settings)

    def counts_by(column: BreakdownColumn) -> dict[str | None, LeadCounts]:
        return lead_counts_by_column(lead_frame, column, window, report_date, config)

    def previous_counts_by(column: BreakdownColumn) -> dict[str | None, LeadCounts] | None:
        if previous_week is None:
            return None
        return lead_counts_by_column(
            previous_week.frame, column, previous_window, previous_week.snapshot_date, config
        )

    by_source, previous_by_source = counts_by("source_name"), previous_counts_by("source_name")
    return WeeklyIrrelevant(
        by_campaign=irrelevant_rows(
            counts_by("utm_campanie"),
            previous_counts_by("utm_campanie"),
            params.min_campaign_leads,
            params.top_rows,
            config,
        ),
        by_source=irrelevant_rows(
            by_source, previous_by_source, params.min_source_leads, params.top_rows, config
        ),
        total_irr=kpis_from(add_counts(by_source.values())).irr,
        total_irr_previous=None
        if previous_by_source is None
        else kpis_from(add_counts(previous_by_source.values())).irr,
        previous_snapshot_date=None if previous_week is None else previous_week.snapshot_date,
    )
