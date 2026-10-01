from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from digest.config import AppConfig
from digest.metrics.breakdown import lead_breakdown
from digest.metrics.daily import PreviousSnapshot
from digest.metrics.daily_checks import (
    MissingFollowupDate,
    OverdueRevenire,
    UntouchedAverage,
    followup_backlog,
    missing_followup_date,
    not_taken_leads,
    overdue_revenire_by_manager,
    rolling_contract_rate,
    shown_percent,
    since_leads_created_from,
    snapshot_period,
    stale_offers,
    untouched_average,
)
from digest.metrics.kpi import (
    LeadCounts,
    Period,
    kpis_from,
    lead_counts,
    lead_counts_by_manager,
    lead_counts_by_showroom,
)
from digest.metrics.monthly import (
    MonthlyCohortConversion,
    MonthlyRepeatClients,
    first_day_of_month,
    last_day_of_month,
    month_window,
    monthly_cohort_conversion,
    monthly_funnel,
    monthly_loss_reasons,
    monthly_repeat_clients,
    monthly_source_conversion,
)
from digest.metrics.touches import manager_touches
from digest.metrics.weekly import week_days
from digest.reports.render import day_ranges_label

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
NO_VALUE = "—"
PERCENT_UNIT = "%"
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


class BaselineFileError(Exception):
    pass


@dataclass(frozen=True)
class BaselineRow:
    id: str
    label: str
    value: Value
    unit: str
    period: str


def month_label(month: date) -> str:
    return f"{MONTH_NAMES[month.month - 1]} {month.year}"


def date_label(report_date: date) -> str:
    return f"на {report_date:%d.%m.%Y}"


def since_label(report_date: date, config: AppConfig) -> str:
    return f"с {config.status_mapping.leads_created_from:%d.%m.%Y}, на {report_date:%d.%m.%Y}"


def last_sunday(report_date: date) -> date:
    # docs/kpi-definitions.md, «Базовая линия», касания: в воскресенье неделя кончается этой датой.
    return report_date - timedelta(days=(report_date.weekday() + 1) % 7)


def month_and_previous(report_date: date) -> tuple[date, date]:
    month = first_day_of_month(report_date)
    return month, first_day_of_month(month - timedelta(days=1))


def month_analysis_dates(report_date: date) -> tuple[tuple[str, date], ...]:
    # Прошлый месяц из того же снапшота, как m8 и m9: created_at и converted_at не меняются.
    month, _ = month_and_previous(report_date)
    return (("cur", report_date), ("prev", month - timedelta(days=1)))


def date_warning(report_date: date) -> str | None:
    if report_date == last_day_of_month(report_date):
        return None
    return (
        f"Внимание: {report_date:%d.%m.%Y} не последний день месяца. Для сравнения baseline "
        "снимается в последний день (docs/baseline-2026-09.md, «Правило сравнения»): строки cur "
        "здесь за неполный месяц."
    )


def stock_rows(
    lead_frame: pd.DataFrame,
    untouched: UntouchedAverage,
    everything: Period,
    since: Period,
    missing: MissingFollowupDate,
    overdue: OverdueRevenire,
    all_counts: LeadCounts,
    report_date: date,
    config: AppConfig,
) -> list[BaselineRow]:
    on_date = date_label(report_date)
    backlog = followup_backlog(lead_frame, everything, report_date, config)
    offers = stale_offers(lead_frame, None, report_date, config)
    month, previous_month = month_and_previous(report_date)
    losses = monthly_loss_reasons(lead_frame, report_date, config)
    not_taken = not_taken_leads(lead_frame, everything, report_date, config)
    min_age_hours = config.kpi.not_taken_min_age_hours
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
            untouched.lead_count_by_day[report_date],
            "лидов",
            on_date,
        ),
        BaselineRow(
            "A5.untouched.cur_average",
            "Лиды без касания (d2), среднее по снапшотам месяца",
            None if untouched.average is None else round(untouched.average, 1),
            "лидов в день",
            f"{len(untouched.lead_count_by_day)} из {report_date.day} дней, {month_label(month)}",
        ),
        BaselineRow("A6.nar", "NU A RASPUNS", all_counts.nar, "лидов", on_date),
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
            since_label(report_date, config),
        ),
        BaselineRow(
            "A7.not_taken.older_1d",
            f"Не взятые открытые лиды старше {min_age_hours} ч",
            not_taken.older_than_min_age,
            "лидов",
            on_date,
        ),
    ]


def flow_rows(
    lead_frame: pd.DataFrame,
    cohort_conversion: MonthlyCohortConversion,
    repeat_clients: MonthlyRepeatClients,
    report_date: date,
    config: AppConfig,
) -> list[BaselineRow]:
    rows: list[BaselineRow] = []
    cohorts = {row.month: row for row in cohort_conversion.cohorts}
    params = config.modules.cohort_conversion_params
    cohort_days = params.baseline_cohort_days
    for suffix, analysis_date in month_analysis_dates(report_date):
        month = first_day_of_month(analysis_date)
        period = month_label(month)
        counts = monthly_funnel(lead_frame, analysis_date, config).company
        kpis = kpis_from(counts)
        cycle = (
            cohort_conversion
            if analysis_date == report_date
            else monthly_cohort_conversion(lead_frame, analysis_date, config)
        ).cycle
        repeat = (
            repeat_clients
            if analysis_date == report_date
            else monthly_repeat_clients(lead_frame, analysis_date, config)
        ).company
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
                f"Договоры за ≤ {params.fast_cycle_days} дней",
                shown_percent(cycle.share_within_fast),
                PERCENT_UNIT,
                period,
            ),
            BaselineRow(
                f"B5.contracts.{suffix}", "Договоры месяца", cycle.contracts, "договоров", period
            ),
            BaselineRow(
                f"B6.cohort_{cohort_days}d.{suffix}",
                f"Конверсия когорты ≤ {cohort_days} дней (m9)",
                shown_percent(cohorts[month].share_within(cohort_days)),
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
    lead_frame: pd.DataFrame,
    since: Period,
    missing: MissingFollowupDate,
    overdue: OverdueRevenire,
    report_date: date,
    config: AppConfig,
) -> list[BaselineRow]:
    on_date = date_label(report_date)
    since_period = since_label(report_date, config)
    month_period = month_label(first_day_of_month(report_date))
    month = month_window(report_date, config.status_mapping.time)
    counts_since = lead_counts_by_manager(lead_frame, since, report_date, config)
    counts_month = lead_counts_by_manager(lead_frame, month, report_date, config)
    backlog = followup_backlog(lead_frame, since, report_date, config)
    rows: list[BaselineRow] = []
    for manager in config.managers.managers:
        if not manager.active:
            continue
        name = manager.name
        for scope, counts, period in (
            ("since", counts_since[manager.id], since_period),
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
                since_period,
            ),
            BaselineRow(
                f"C.{name}.since.nar",
                f"{name}: NU A RASPUNS",
                counts_since[manager.id].nar,
                "лидов",
                since_period,
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
    days = week_days(last_sunday(report_date))
    touches = manager_touches(touch_snapshots, days, config)
    period = f"{days[0]:%d.%m}–{days[-1]:%d.%m.%Y}"
    # Пометки неполной недели те же, что сноски w14 (manager_touches.j2).
    if touches.covered_from is None:
        period += ", нет пары снапшотов"
    else:
        if touches.covered_from > days[0]:
            period += f", посчитано с {touches.covered_from:%d.%m}"
        if touches.days_without_snapshot:
            period += f", без снапшота: {day_ranges_label(touches.days_without_snapshot)}"
        if touches.covered_from > days[0] or touches.days_without_snapshot:
            period += ", неделя неполная"
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
    lead_frame: pd.DataFrame, everything: Period, report_date: date, config: AppConfig
) -> list[BaselineRow]:
    all_time = f"всё время, {date_label(report_date)}"
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
            "D.leads.other",
            f"Лиды: источники меньше {params.min_source_leads} лидов",
            0 if by_source.other is None else by_source.other.counts.leads,
            "лидов",
            all_time,
        ),
        BaselineRow(
            "D.leads.none",
            "Лиды без источника",
            0 if by_source.without_key is None else by_source.without_key.counts.leads,
            "лидов",
            all_time,
        ),
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
    lead_frame: pd.DataFrame,
    everything: Period,
    since: Period,
    all_counts: LeadCounts,
    repeat_clients: MonthlyRepeatClients,
    report_date: date,
    config: AppConfig,
) -> list[BaselineRow]:
    on_date = date_label(report_date)
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
            since_label(report_date, config),
        ),
        BaselineRow(
            "F.unmapped",
            "Без статуса или статус не из маппинга",
            all_counts.unmapped,
            "лидов",
            on_date,
        ),
        BaselineRow(
            "F.won_without_converted_at",
            "Clienți без даты конверсии",
            repeat_clients.won_without_converted_at,
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
    # docs/baseline-2026-09.md: каждое число из функции metrics/ (инвариант 1). month_frames
    # содержит кадр даты: он же кадр A5 на дату.
    everything = snapshot_period(report_date, config)
    since = since_leads_created_from(report_date, config)
    missing = missing_followup_date(lead_frame, report_date, config)
    overdue = overdue_revenire_by_manager(lead_frame, report_date, config)
    all_counts = lead_counts(lead_frame, everything, report_date, config)
    repeat_clients = monthly_repeat_clients(lead_frame, report_date, config)
    return [
        *stock_rows(
            lead_frame,
            untouched_average(month_frames, config),
            everything,
            since,
            missing,
            overdue,
            all_counts,
            report_date,
            config,
        ),
        *flow_rows(
            lead_frame,
            monthly_cohort_conversion(lead_frame, report_date, config),
            repeat_clients,
            report_date,
            config,
        ),
        *consultant_rows(lead_frame, since, missing, overdue, report_date, config),
        *touch_rows_of_week(touch_snapshots, report_date, config),
        *source_rows(lead_frame, everything, report_date, config),
        *data_quality_rows(
            lead_frame, everything, since, all_counts, repeat_clients, report_date, config
        ),
        *(
            BaselineRow(row_id, label, None, NO_SOURCE_UNIT, NO_VALUE)
            for row_id, label in METRICS_WITHOUT_SOURCE
        ),
    ]


def shown_value(value: Value) -> str:
    if value is None:
        return NO_VALUE
    if isinstance(value, int):
        return str(value)
    return f"{value:.1f}"


def parsed_value(text: str) -> Value:
    if text == NO_VALUE:
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
    lines = list(lines)
    if TABLE_HEADER[0] not in lines:
        raise BaselineFileError(f"нет строки заголовка «{TABLE_HEADER[0]}»")
    rows = []
    for line_number, line in enumerate(lines, start=1):
        if not line.startswith("| ") or line in TABLE_HEADER:
            continue
        cells = [cell.strip() for cell in line.strip("|").split(" | ")]
        if len(cells) != 5:
            raise BaselineFileError(
                f"строка {line_number}: ячеек {len(cells)}, а нужно 5 (id, метрика, значение, "
                "ед., период)"
            )
        row_id, label, value, unit, period = cells
        try:
            parsed = parsed_value(value)
        except ValueError:
            raise BaselineFileError(
                f"строка {line_number}: значение «{value}» не число и не «{NO_VALUE}»"
            ) from None
        rows.append(BaselineRow(row_id, label, parsed, unit, period))
    return rows


def parse_baseline_file(path: Path) -> list[BaselineRow]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise BaselineFileError(f"файл не прочитан ({error.strerror})") from None
    return parse_baseline_lines(text.splitlines())


def difference(base: BaselineRow, current: BaselineRow) -> str:
    # Разные единицы (метрика переопределена между прогонами) не вычитаются.
    if base.value is None or current.value is None or base.unit != current.unit:
        return NO_VALUE
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
        lines.append(table_line((row_id, row.label, shown_value(row.value), NO_VALUE, NO_VALUE)))
    for row_id in only_current:
        row = current_by_id[row_id]
        lines.append(table_line((row_id, row.label, NO_VALUE, shown_value(row.value), NO_VALUE)))
    return lines
