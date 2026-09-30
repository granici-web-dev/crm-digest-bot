from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from digest.config import AppConfig
from digest.metrics.breakdown import lead_breakdown
from digest.metrics.daily import PreviousSnapshot, daily_window
from digest.metrics.daily_checks import (
    followup_backlog,
    missing_followup_date,
    not_taken_leads,
    overdue_revenire_by_manager,
    rolling_contract_rate,
    shown_percent,
    snapshot_period,
    stale_offers,
    untouched_leads,
)
from digest.metrics.kpi import (
    Period,
    kpis_from,
    lead_counts,
    lead_counts_by_manager,
    lead_counts_by_showroom,
)
from digest.metrics.monthly import (
    first_day_of_month,
    month_window,
    monthly_cohort_conversion,
    monthly_funnel,
    monthly_loss_reasons,
    monthly_repeat_clients,
    monthly_source_conversion,
)
from digest.metrics.touches import manager_touches

MONTH_NAMES = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)
NO_SOURCE_UNIT = "нет источника"
PERCENT_UNIT = "%"
COHORT_DAYS = 30
TABLE_HEADER = ("| id | Метрика | Значение | Ед. | Период |", "|---|---|---|---|---|")
COMPARISON_HEADER = ("| id | Метрика | База | Сейчас | Δ |", "|---|---|---|---|---|")
# Метрики файла владельца и реестра договоров: в mefi их нет, строка держит id для сравнения.
METRICS_WITHOUT_SOURCE = (
    ("D4.site_leads", "Лиды с сайта в месяц (файл владельца, без Showroom)"),
    ("D5.cpl", "CPL"),
    ("D6.site_sessions", "Трафик сайта"),
    ("E1.contracts", "Договоров в месяц (реестр)"),
    ("E2.contract_sum", "Сумма договоров в месяц"),
    ("E3.average_contract", "Средний договор"),
    ("E4.roas", "ROAS"),
    ("E5.cac", "CAC"),
    ("F.showroom_revisits", "Повторные визиты в шоурум"),
    ("F.visit_source", "Источник визита"),
)

type Value = int | float | None


@dataclass(frozen=True)
class BaselineRow:
    id: str
    label: str
    value: Value
    unit: str
    period: str


def month_label(month: date) -> str:
    return f"{MONTH_NAMES[month.month - 1]} {month.year}"


def touch_week(report_date: date) -> tuple[date, date]:
    # Последняя полная неделя Пн–Вс, которая кончается не позже даты.
    last_sunday = report_date - timedelta(days=(report_date.weekday() + 1) % 7)
    return last_sunday - timedelta(days=6), last_sunday


def stock_rows(
    lead_frame: pd.DataFrame,
    month_frames: dict[date, pd.DataFrame],
    report_date: date,
    config: AppConfig,
) -> list[BaselineRow]:
    on_date = f"на {report_date:%d.%m.%Y}"
    everything = snapshot_period(report_date, config)
    since = since_created_from(report_date, config)
    since_label = since_created_from_label(report_date, config)
    backlog = followup_backlog(lead_frame, everything, report_date, config)
    missing = missing_followup_date(lead_frame, report_date, config)
    overdue = overdue_revenire_by_manager(lead_frame, report_date, config)
    offers = stale_offers(lead_frame, None, report_date, config)
    month, previous_month = month_and_previous(report_date)
    losses = monthly_loss_reasons(lead_frame, report_date, config)
    not_taken = not_taken_leads(lead_frame, everything, report_date, config)
    untouched_by_day = [
        untouched_leads(frame, day, config).lead_count for day, frame in month_frames.items()
    ]
    average_period = f"{len(untouched_by_day)} из {report_date.day} дней, {month_label(month)}"
    return [
        BaselineRow("A1.stand_by", "Stand BY", backlog.lead_count, "лидов", on_date),
        BaselineRow(
            "A1.stand_by.with_offer", "Stand BY с офертой", backlog.with_offer, "лидов", on_date
        ),
        BaselineRow(
            "A1.stand_by.revenire_due",
            "Stand BY с Data revenire раньше даты",
            backlog.revenire_due,
            "лидов",
            on_date,
        ),
        *(
            BaselineRow(
                f"A1.stand_by.older_{days}d",
                f"Stand BY в статусе дольше {days} дней",
                lead_count,
                "лидов",
                on_date,
            )
            for days, lead_count in backlog.older_than_days.items()
        ),
        BaselineRow(
            "A2.missing_followup_date",
            "Без Data revenire (d3)",
            missing.lead_count,
            "лидов",
            on_date,
        ),
        BaselineRow(
            "A2.unreadable",
            "Data revenire не прочитана",
            missing.unreadable_count,
            "лидов",
            on_date,
        ),
        BaselineRow(
            "A3.overdue_revenire",
            "Просроченные revenire (d3)",
            overdue.lead_count,
            "лидов",
            on_date,
        ),
        BaselineRow(
            "A3.max_days_overdue",
            "Самая старая просрочка",
            overdue.max_days_overdue,
            "дней",
            on_date,
        ),
        BaselineRow("A4.stale_offers", "Оферты без движения (d4)", offers.total, "оферт", on_date),
        BaselineRow(
            "A4.offers_in_work", "Оферты в работе", offers.offers_in_work, "оферт", on_date
        ),
        BaselineRow(
            "A5.untouched",
            "Лиды без касания (d2)",
            untouched_leads(lead_frame, report_date, config).lead_count,
            "лидов",
            on_date,
        ),
        BaselineRow(
            "A5.untouched.cur_average",
            "Лиды без касания (d2), среднее по снапшотам месяца",
            round(sum(untouched_by_day) / len(untouched_by_day), 1) if untouched_by_day else None,
            "лидов в день",
            average_period,
        ),
        BaselineRow(
            "A6.nar",
            "NU A RASPUNS",
            lead_counts(lead_frame, everything, report_date, config).nar,
            "лидов",
            on_date,
        ),
        BaselineRow(
            "A6.nar.cur",
            "Новых NU A RASPUNS за месяц",
            losses.current.reason_total("NU_RASPUNS"),
            "лидов",
            month_label(month),
        ),
        BaselineRow(
            "A6.nar.prev",
            "Новых NU A RASPUNS за месяц",
            losses.previous.reason_total("NU_RASPUNS"),
            "лидов",
            month_label(previous_month),
        ),
        BaselineRow(
            "A7.not_taken", "Не взятые открытые лиды", not_taken.lead_count, "лидов", on_date
        ),
        BaselineRow(
            "A7.not_taken.since",
            "Не взятые открытые лиды",
            not_taken_leads(lead_frame, since, report_date, config).lead_count,
            "лидов",
            since_label,
        ),
        BaselineRow(
            "A7.not_taken.older_1d",
            "Не взятые открытые лиды старше суток",
            not_taken.older_than_day,
            "лидов",
            on_date,
        ),
    ]


def month_and_previous(report_date: date) -> tuple[date, date]:
    month = first_day_of_month(report_date)
    return month, first_day_of_month(month - timedelta(days=1))


def month_analysis_dates(report_date: date) -> tuple[tuple[str, date], ...]:
    # Прошлый месяц из того же снапшота, как m8 и m9: created_at и converted_at не меняются.
    month, _ = month_and_previous(report_date)
    return (("cur", report_date), ("prev", month - timedelta(days=1)))


def since_created_from(report_date: date, config: AppConfig) -> Period:
    status_mapping = config.status_mapping
    return Period(
        datetime.combine(
            status_mapping.leads_created_from, time(), tzinfo=ZoneInfo(status_mapping.time.timezone)
        ),
        daily_window(report_date, status_mapping.time).end,
    )


def since_created_from_label(report_date: date, config: AppConfig) -> str:
    return f"с {config.status_mapping.leads_created_from:%d.%m.%Y}, на {report_date:%d.%m.%Y}"


def flow_rows(lead_frame: pd.DataFrame, report_date: date, config: AppConfig) -> list[BaselineRow]:
    rows: list[BaselineRow] = []
    cohorts = {
        row.month: row for row in monthly_cohort_conversion(lead_frame, report_date, config).cohorts
    }
    fast_days = config.modules.cohort_conversion_params.fast_cycle_days
    for suffix, analysis_date in month_analysis_dates(report_date):
        month = first_day_of_month(analysis_date)
        period = month_label(month)
        counts = monthly_funnel(lead_frame, analysis_date, config).company
        kpis = kpis_from(counts)
        cycle = monthly_cohort_conversion(lead_frame, analysis_date, config).cycle
        repeat = monthly_repeat_clients(lead_frame, analysis_date, config).company
        rows += [
            BaselineRow(
                f"B1.scr.{suffix}", "SCR месяца (m4)", shown_percent(kpis.scr), PERCENT_UNIT, period
            ),
            BaselineRow(f"B1.clienti.{suffix}", "Клиенты когорты", counts.clienti, "лидов", period),
            BaselineRow(
                f"B1.useful.{suffix}", "Полезные лиды когорты", counts.useful, "лидов", period
            ),
            BaselineRow(f"B2.l2o.{suffix}", "L2O", shown_percent(kpis.l2o), PERCENT_UNIT, period),
            BaselineRow(f"B2.offers.{suffix}", "Оферты когорты", counts.offers, "лидов", period),
            BaselineRow(f"B3.o2c.{suffix}", "O2C", shown_percent(kpis.o2c), PERCENT_UNIT, period),
            BaselineRow(
                f"B5.cycle_median.{suffix}",
                "Цикл сделки, медиана (m9)",
                cycle.median_days,
                "дней",
                period,
            ),
            BaselineRow(
                f"B5.cycle_p75.{suffix}", "Цикл сделки, P75", cycle.p75_days, "дней", period
            ),
            BaselineRow(
                f"B5.fast_share.{suffix}",
                f"Договоры за ≤ {fast_days} дней",
                shown_percent(cycle.share_within_fast),
                PERCENT_UNIT,
                period,
            ),
            BaselineRow(
                f"B5.contracts.{suffix}", "Договоры месяца", cycle.contracts, "договоров", period
            ),
            BaselineRow(
                f"B6.cohort_{COHORT_DAYS}d.{suffix}",
                f"Конверсия когорты ≤ {COHORT_DAYS} дней (m9)",
                shown_percent(cohorts[month].share_within(COHORT_DAYS)),
                PERCENT_UNIT,
                period,
            ),
            BaselineRow(
                f"B7.repeat.{suffix}", "Повторные клиенты (m11)", repeat.repeat, "клиентов", period
            ),
            BaselineRow(
                f"B7.clients.{suffix}", "Клиенты месяца (m11)", repeat.clients, "клиентов", period
            ),
        ]
    rate = rolling_contract_rate(lead_frame, report_date, config)
    window_label = f"{rate.window_days} дней по {report_date:%d.%m.%Y}"
    previous_label = f"предыдущие {rate.window_days} дней"
    rows += [
        BaselineRow(
            "B4.contract_rate",
            "Доля договоров (d7)",
            shown_percent(rate.rate),
            PERCENT_UNIT,
            window_label,
        ),
        BaselineRow(
            "B4.contracts", "Договоры окна (d7)", rate.contracts, "договоров", window_label
        ),
        BaselineRow("B4.useful", "Полезные лиды окна (d7)", rate.useful, "лидов", window_label),
        BaselineRow(
            "B4.contract_rate.previous",
            "Доля договоров (d7)",
            shown_percent(rate.previous_rate),
            PERCENT_UNIT,
            previous_label,
        ),
    ]
    return rows


def consultant_rows(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> list[BaselineRow]:
    on_date = f"на {report_date:%d.%m.%Y}"
    since = since_created_from(report_date, config)
    since_label = since_created_from_label(report_date, config)
    month_period = month_label(first_day_of_month(report_date))
    month = month_window(report_date, config.status_mapping.time)
    counts_since = lead_counts_by_manager(lead_frame, since, report_date, config)
    counts_month = lead_counts_by_manager(lead_frame, month, report_date, config)
    backlog = followup_backlog(lead_frame, since, report_date, config)
    missing = missing_followup_date(lead_frame, report_date, config)
    overdue = overdue_revenire_by_manager(lead_frame, report_date, config)
    rows: list[BaselineRow] = []
    for manager in config.managers.managers:
        if not manager.active:
            continue
        name = manager.name
        for scope, counts, period in (
            ("since", counts_since[manager.id], since_label),
            ("cur", counts_month[manager.id], month_period),
        ):
            kpis = kpis_from(counts)
            rows += [
                BaselineRow(
                    f"C.{name}.{scope}.leads", f"{name}: лидов", counts.leads, "лидов", period
                ),
                BaselineRow(
                    f"C.{name}.{scope}.scr",
                    f"{name}: SCR",
                    shown_percent(kpis.scr),
                    PERCENT_UNIT,
                    period,
                ),
                BaselineRow(
                    f"C.{name}.{scope}.o2c",
                    f"{name}: O2C",
                    shown_percent(kpis.o2c),
                    PERCENT_UNIT,
                    period,
                ),
            ]
        rows += [
            BaselineRow(
                f"C.{name}.since.stand_by",
                f"{name}: Stand BY",
                backlog.lead_count_of(name),
                "лидов",
                since_label,
            ),
            BaselineRow(
                f"C.{name}.since.nar",
                f"{name}: NU A RASPUNS",
                counts_since[manager.id].nar,
                "лидов",
                since_label,
            ),
            BaselineRow(
                f"C.{name}.missing_followup_date",
                f"{name}: без Data revenire (d3)",
                missing.of_manager(name).lead_count,
                "лидов",
                on_date,
            ),
            BaselineRow(
                f"C.{name}.overdue_revenire",
                f"{name}: просроченные revenire (d3)",
                overdue.of_manager(name).lead_count,
                "лидов",
                on_date,
            ),
        ]
    return rows


def touch_rows_of_week(
    touch_snapshots: tuple[PreviousSnapshot, ...], report_date: date, config: AppConfig
) -> list[BaselineRow]:
    first_day, last_day = touch_week(report_date)
    days = tuple(first_day + timedelta(days=offset) for offset in range(7))
    touches = manager_touches(touch_snapshots, days, config)
    period = f"{first_day:%d.%m}–{last_day:%d.%m.%Y}"
    if touches.covered_from is None:
        period += ", нет пары снапшотов"
    elif touches.covered_from > first_day or touches.days_without_snapshot:
        period += f", снапшоты с {touch_snapshots[0].snapshot_date:%d.%m}, неделя неполная"
    counted = touches.covered_from is not None
    rows = [
        BaselineRow(
            "C.touch.total",
            "Касания Revenire (w14)",
            touches.touch_count if counted else None,
            "касаний",
            period,
        )
    ]
    for manager in config.managers.managers:
        if manager.active:
            rows.append(
                BaselineRow(
                    f"C.{manager.name}.touch",
                    f"{manager.name}: касания Revenire (w14)",
                    touches.of_manager(manager.name).touch_count if counted else None,
                    "касаний",
                    period,
                )
            )
    return rows


def source_rows(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> list[BaselineRow]:
    all_time = f"всё время, на {report_date:%d.%m.%Y}"
    everything = snapshot_period(report_date, config)
    params = config.modules.scr_by_source_campaign_params
    by_source = lead_breakdown(
        lead_frame, "source_name", everything, report_date, config, params.min_source_leads
    )
    by_campaign = lead_breakdown(
        lead_frame, "utm_campanie", everything, report_date, config, params.min_campaign_leads
    )
    month_campaigns = monthly_source_conversion(lead_frame, report_date, config)
    rows: list[BaselineRow] = []
    for row in by_source.rows:
        rows += [
            BaselineRow(
                f"D.leads.{row.key}", f"Лиды: {row.key}", row.counts.leads, "лидов", all_time
            ),
            BaselineRow(
                f"D1.irr.{row.key}",
                f"IRR: {row.key}",
                shown_percent(row.kpis.irr),
                PERCENT_UNIT,
                all_time,
            ),
            BaselineRow(
                f"D2.scr.{row.key}",
                f"SCR: {row.key}",
                shown_percent(row.kpis.scr),
                PERCENT_UNIT,
                all_time,
            ),
        ]
    rows += [
        BaselineRow(
            "D3.utm_leads", "Лиды с UTM_Campanie", by_campaign.leads_with_key, "лидов", all_time
        ),
        BaselineRow(
            "D3.utm_share",
            "Доля лидов с UTM_Campanie",
            shown_percent(by_campaign.key_share),
            PERCENT_UNIT,
            all_time,
        ),
        BaselineRow(
            "D3.utm_share.cur",
            "Доля лидов с UTM_Campanie",
            shown_percent(month_campaigns.campaign_share),
            PERCENT_UNIT,
            month_label(month_campaigns.month),
        ),
    ]
    return rows


def data_quality_rows(
    lead_frame: pd.DataFrame, report_date: date, config: AppConfig
) -> list[BaselineRow]:
    on_date = f"на {report_date:%d.%m.%Y}"
    everything = snapshot_period(report_date, config)
    since = since_created_from(report_date, config)
    since_label = since_created_from_label(report_date, config)
    return [
        BaselineRow(
            "F.no_showroom",
            "Без шоурума",
            lead_counts_by_showroom(lead_frame, everything, report_date, config)[None].leads,
            "лидов",
            on_date,
        ),
        BaselineRow(
            "F.no_showroom.since",
            "Без шоурума",
            lead_counts_by_showroom(lead_frame, since, report_date, config)[None].leads,
            "лидов",
            since_label,
        ),
        BaselineRow(
            "F.unmapped",
            "Без статуса или статус не из маппинга",
            lead_counts(lead_frame, everything, report_date, config).unmapped,
            "лидов",
            on_date,
        ),
        BaselineRow(
            "F.won_without_converted_at",
            "Clienți без даты конверсии",
            monthly_repeat_clients(lead_frame, report_date, config).won_without_converted_at,
            "лидов",
            on_date,
        ),
    ]


def baseline_rows(
    lead_frame: pd.DataFrame,
    month_frames: dict[date, pd.DataFrame],
    touch_snapshots: tuple[PreviousSnapshot, ...],
    report_date: date,
    config: AppConfig,
) -> list[BaselineRow]:
    # docs/baseline-2026-09.md: каждое число это вызов функции metrics/, как в отчётах и чате
    # (инвариант 1). Здесь только раскладка по строкам.
    return [
        *stock_rows(lead_frame, month_frames, report_date, config),
        *flow_rows(lead_frame, report_date, config),
        *consultant_rows(lead_frame, report_date, config),
        *touch_rows_of_week(touch_snapshots, report_date, config),
        *source_rows(lead_frame, report_date, config),
        *data_quality_rows(lead_frame, report_date, config),
        *(
            BaselineRow(row_id, label, None, NO_SOURCE_UNIT, "—")
            for row_id, label in METRICS_WITHOUT_SOURCE
        ),
    ]


def shown_value(value: Value) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return str(value)
    return f"{value:.1f}"


def parsed_value(text: str) -> Value:
    if text == "—":
        return None
    if text.lstrip("-").isdigit():
        return int(text)
    return float(text)


def table_line(cells: Iterable[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def markdown_lines(rows: list[BaselineRow], report_date: date, config: AppConfig) -> list[str]:
    window_end = config.status_mapping.time.daily_window_end
    return [
        f"# Baseline {report_date:%d.%m.%Y}",
        f"Снапшот: {report_date:%d.%m.%Y} {window_end:%H:%M} · "
        f"команда: python -m digest baseline --date {report_date.isoformat()}",
        "",
        *TABLE_HEADER,
        *(
            table_line((row.id, row.label, shown_value(row.value), row.unit, row.period))
            for row in rows
        ),
    ]


def parse_baseline_lines(lines: Iterable[str]) -> list[BaselineRow]:
    rows = []
    for line in lines:
        if not line.startswith("| ") or line in TABLE_HEADER:
            continue
        row_id, label, value, unit, period = (cell.strip() for cell in line.strip("|").split(" | "))
        rows.append(BaselineRow(row_id, label, parsed_value(value), unit, period))
    return rows


def difference(base: BaselineRow, current: BaselineRow) -> str:
    if base.value is None or current.value is None:
        return "—"
    change = current.value - base.value
    if current.unit == PERCENT_UNIT:
        return f"{change:+.1f} п.п."
    return f"{change:+d}" if isinstance(change, int) else f"{change:+.1f}"


def comparison_lines(base: list[BaselineRow], current: list[BaselineRow]) -> list[str]:
    base_by_id = {row.id: row for row in base}
    current_by_id = {row.id: row for row in current}
    both = [row.id for row in base if row.id in current_by_id]
    only_base = [row.id for row in base if row.id not in current_by_id]
    only_current = [row.id for row in current if row.id not in base_by_id]
    lines = list(COMPARISON_HEADER)
    for row_id in both:
        before, after = base_by_id[row_id], current_by_id[row_id]
        lines.append(
            table_line(
                (
                    row_id,
                    after.label,
                    shown_value(before.value),
                    shown_value(after.value),
                    difference(before, after),
                )
            )
        )
    for row_id in only_base:
        row = base_by_id[row_id]
        lines.append(table_line((row_id, row.label, shown_value(row.value), "—", "—")))
    for row_id in only_current:
        row = current_by_id[row_id]
        lines.append(table_line((row_id, row.label, "—", shown_value(row.value), "—")))
    return lines
