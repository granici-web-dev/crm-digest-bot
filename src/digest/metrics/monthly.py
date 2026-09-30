from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

from digest.config import AppConfig, TimeSettings
from digest.metrics.breakdown import LeadBreakdown, lead_breakdown
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
    LEAD_ROW_COLUMNS,
    LossReasons,
    converted_count,
    lead_rows,
    loss_reasons_in_window,
    relative_change,
    showroom_keys,
)

TREND_MONTHS = 6
BELOW_ALL_LEVELS = "below_levels"
# Причина повторного клиента m11, значение столбца repeat_reason.
REPEAT_BY_CONTACT = "contact"
REPEAT_BY_SOURCE = "source"
SECONDS_PER_DAY = 86_400


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
class MonthlySourceConversion:
    month: date
    by_source: LeadBreakdown
    # without_key в m7 не выводится: это лиды без кампании, их число дают leads_total и доля.
    by_campaign: LeadBreakdown

    @property
    def leads_total(self) -> int:
        return self.by_source.total.counts.leads

    @property
    def leads_with_campaign(self) -> int:
        return self.by_campaign.leads_with_key

    @property
    def campaign_share(self) -> float | None:
        return self.by_campaign.key_share


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


@dataclass(frozen=True)
class RepeatClientCounts:
    clients: int
    repeat: int
    # Часть repeat: источник из sources.repeat_client без совпадения контакта.
    by_source: int = 0

    @property
    def by_contact(self) -> int:
        return self.repeat - self.by_source

    @property
    def share(self) -> float | None:
        return ratio(self.repeat, self.clients)


@dataclass(frozen=True)
class MonthlyRepeatClients:
    month: date
    company: RepeatClientCounts
    by_showroom: dict[str | None, RepeatClientCounts]
    # Лид Clienți без converted_at нельзя отнести к месяцу: в расчёт не входит, считается по
    # всему снапшоту для сноски.
    won_without_converted_at: int


@dataclass(frozen=True)
class CohortRow:
    month: date
    counts: LeadCounts
    clients_with_date: int
    # Корзина ≤N дней → клиентов когорты; None, пока когорте меньше N суток.
    clients_within_days: dict[int, int | None]
    median_days: float | None
    age_days: float
    in_progress: bool

    @property
    def conversion(self) -> float | None:
        return kpis_from(self.counts).scr

    def share_within(self, days: int) -> float | None:
        # docs/kpi-definitions.md, «Когорты и цикл сделки (m9)»: знаменатель USEFUL, как у SCR.
        clients = self.clients_within_days[days]
        return None if clients is None else ratio(clients, self.counts.useful)


@dataclass(frozen=True)
class DealCycle:
    contracts: int
    median_days: float | None
    p75_days: float | None
    within_fast_days: int

    @property
    def share_within_fast(self) -> float | None:
        return ratio(self.within_fast_days, self.contracts)


@dataclass(frozen=True)
class MonthlyCohortConversion:
    report_month: date
    cohorts: tuple[CohortRow, ...]
    cohorts_by_showroom: dict[str | None, tuple[CohortRow, ...]]
    cycle: DealCycle
    cycle_by_showroom: dict[str | None, DealCycle]

    @property
    def clients_without_converted_at(self) -> int:
        return sum(row.counts.clienti - row.clients_with_date for row in self.cohorts)


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


def monthly_source_conversion(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> MonthlySourceConversion:
    # Когорта лидов месяца на дату снапшота, та же, что воронка m2: итог m7 равен компании m2
    # (docs/kpi-definitions.md, «Разбивка по источникам и кампаниям»).
    window = month_window(report_date, config.status_mapping.time)
    params = config.modules.scr_by_source_campaign_params
    return MonthlySourceConversion(
        first_day_of_month(report_date),
        lead_breakdown(
            lead_frame, "source_name", window, report_date, config, params.min_source_leads
        ),
        lead_breakdown(
            lead_frame, "utm_campanie", window, report_date, config, params.min_campaign_leads
        ),
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


def repeat_client_reasons(lead_frame: pd.DataFrame, config: AppConfig) -> pd.Series:
    # docs/kpi-definitions.md, «Месячное окно», m11. Совпадение контакта проверяется первым:
    # оно доказывает покупку в mefi, источник это слово продавца. Клиент с обоими признаками
    # получает одну причину и считается один раз.
    clients = lead_frame[lead_frame["is_clienti"] & lead_frame["converted_at"].notna()]
    by_contact = contact_repeat_flags(clients)
    by_source = clients["source_name"].isin(config.status_mapping.sources.repeat_client)
    # Не через присваивание по маске: оно превращает None в NaN.
    return pd.Series(
        [
            REPEAT_BY_CONTACT if contact else REPEAT_BY_SOURCE if source else None
            for contact, source in zip(by_contact, by_source, strict=True)
        ],
        index=clients.index,
        dtype=object,
    )


def contact_repeat_flags(clients: pd.DataFrame) -> pd.Series:
    # Непустой ключ телефона или e-mail совпал с другим лидом WON, чей converted_at строго
    # раньше. Сравнение внутри одного снапшота, как revenire d1 (ADR-006); равные converted_at
    # друг друга не повторяют.
    flags = pd.Series(False, index=clients.index)
    earlier_phone_keys: set[str] = set()
    earlier_email_keys: set[str] = set()
    for _, same_moment in clients.groupby("converted_at", sort=True):
        flags[same_moment.index] = same_moment["contact_phone_key"].isin(
            earlier_phone_keys
        ) | same_moment["contact_email_key"].isin(earlier_email_keys)
        earlier_phone_keys.update(same_moment["contact_phone_key"].dropna())
        earlier_email_keys.update(same_moment["contact_email_key"].dropna())
    return flags


def month_clients(lead_frame: pd.DataFrame, report_date: date, config: AppConfig) -> pd.DataFrame:
    window = month_window(report_date, config.status_mapping.time)
    converted_at = lead_frame["converted_at"]
    in_window = converted_at.ge(window.start) & converted_at.lt(window.end)
    reasons = repeat_client_reasons(lead_frame, config)
    return lead_frame[lead_frame["is_clienti"] & in_window].assign(
        repeat_reason=reasons, is_repeat=reasons.notna()
    )


def repeat_client_counts(clients: pd.DataFrame) -> RepeatClientCounts:
    return RepeatClientCounts(
        len(clients),
        int(clients["is_repeat"].sum()),
        int(clients["repeat_reason"].eq(REPEAT_BY_SOURCE).sum()),
    )


def monthly_repeat_clients(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> MonthlyRepeatClients:
    clients = month_clients(lead_frame, report_date, config)
    return MonthlyRepeatClients(
        first_day_of_month(report_date),
        repeat_client_counts(clients),
        {
            showroom: repeat_client_counts(
                clients[
                    clients["showroom"].isna()
                    if showroom is None
                    else clients["showroom"].eq(showroom)
                ]
            )
            for showroom in showroom_keys(clients["showroom"], config)
        },
        int((lead_frame["is_clienti"] & lead_frame["converted_at"].isna()).sum()),
    )


def monthly_client_rows(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> pd.DataFrame:
    # Те же клиенты, что «din N» в m11; день листа это день converted_at по Бухаресту.
    clients = month_clients(lead_frame, report_date, config)
    rows = clients.assign(day=clients["converted_at"].dt.tz_localize(None).dt.date)
    return rows.sort_values(["day", "converted_at"])[
        [*LEAD_ROW_COLUMNS, "is_repeat", "repeat_reason"]
    ].reset_index(drop=True)


def days_to_contract(leads: pd.DataFrame) -> pd.Series:
    # docs/kpi-definitions.md, «Когорты и цикл сделки (m9)»: прошедшее время aware-datetime,
    # переход на летнее время не сдвигает дни. converted_at раньше created_at (лид заведён
    # задним числом) считается договором в день обращения, 0 дней.
    elapsed = (leads["converted_at"] - leads["created_at"]).dt.total_seconds() / SECONDS_PER_DAY
    return elapsed.clip(lower=0)


def median_or_none(days: pd.Series) -> float | None:
    return None if days.empty else float(days.median())


def cohort_row(
    lead_frame: pd.DataFrame, month: date, report_date: date, config: AppConfig
) -> CohortRow:
    # docs/kpi-definitions.md, «Когорты и цикл сделки (m9)». Возраст когорты между концами окон:
    # при возрасте ≥ N суток каждый лид когорты прожил не меньше N суток, корзина ≤N полная.
    time_settings = config.status_mapping.time
    window = month_window(month, time_settings)
    age_days = (
        month_window(report_date, time_settings).end - window.end
    ).total_seconds() / SECONDS_PER_DAY
    leads = leads_in_period(lead_frame, window)
    clients = leads[leads["is_clienti"] & leads["converted_at"].notna()]
    days = days_to_contract(clients)
    buckets = config.modules.cohort_conversion_params.age_buckets_days
    return CohortRow(
        month,
        lead_counts(lead_frame, window, report_date, config),
        len(clients),
        {bucket: int(days.le(bucket).sum()) if age_days >= bucket else None for bucket in buckets},
        median_or_none(days),
        age_days,
        age_days < buckets[-1],
    )


def deal_cycle(converted: pd.DataFrame, fast_cycle_days: int) -> DealCycle:
    days = days_to_contract(converted)
    return DealCycle(
        len(days),
        median_or_none(days),
        # Квантиль с линейной интерполяцией pandas (docs/kpi-definitions.md, m9).
        None if days.empty else float(days.quantile(0.75)),
        int(days.le(fast_cycle_days).sum()),
    )


def leads_of_showroom(lead_frame: pd.DataFrame, showroom: str | None) -> pd.DataFrame:
    showrooms = lead_frame["showroom"]
    return lead_frame[showrooms.isna() if showroom is None else showrooms.eq(showroom)]


def monthly_cohort_conversion(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> MonthlyCohortConversion:
    # Все когорты из одного снапшота: created_at и converted_at не меняются. Клиент когорты по
    # статусу Clienți, как CLIENTI в m2 и m4; converted_at только для дней. Цикл месяца по тем же
    # договорам, что «Contracte» m3 (docs/kpi-definitions.md, «Когорты и цикл сделки (m9)»).
    time_settings = config.status_mapping.time
    months = trend_months(report_date)
    report_window = month_window(report_date, time_settings)
    converted_at = lead_frame["converted_at"]
    converted = lead_frame[
        converted_at.ge(report_window.start) & converted_at.lt(report_window.end)
    ]
    six_months = leads_in_period(
        lead_frame, Period(month_window(months[0], time_settings).start, report_window.end)
    )
    showrooms = showroom_keys(pd.concat([six_months["showroom"], converted["showroom"]]), config)
    fast_cycle_days = config.modules.cohort_conversion_params.fast_cycle_days

    def cohorts_of(leads: pd.DataFrame) -> tuple[CohortRow, ...]:
        return tuple(cohort_row(leads, month, report_date, config) for month in months)

    return MonthlyCohortConversion(
        first_day_of_month(report_date),
        cohorts_of(lead_frame),
        {showroom: cohorts_of(leads_of_showroom(lead_frame, showroom)) for showroom in showrooms},
        deal_cycle(converted, fast_cycle_days),
        {
            showroom: deal_cycle(leads_of_showroom(converted, showroom), fast_cycle_days)
            for showroom in showrooms
        },
    )
