from datetime import date

import pandas as pd

from digest.config import AppConfig
from digest.metrics.weekly import (
    DayShowroomCounts,
    WeeklyLeadTables,
    relative_change,
    week_over_week,
    weekly_funnel,
    weekly_lead_tables,
    weekly_loss_reasons,
    weekly_showroom_visits,
)
from digest.reports.context import ModuleResult, ReportContext
from digest.reports.render import RO_WEEKDAYS, render

TableRow = list[str | int]

WITHOUT_SHOWROOM = "(fără showroom)"
WITHOUT_SOURCE = "(fără sursă)"


def showroom_label(showroom: str | None) -> str:
    return WITHOUT_SHOWROOM if showroom is None else showroom


def source_label(source: str | None) -> str:
    return WITHOUT_SOURCE if source is None else source


def short_day_label(day: date) -> str:
    return f"{day:%d-%m} {RO_WEEKDAYS[day.weekday()]}"


def long_day_label(day: date) -> str:
    return f"{day:%d-%m-%Y}  {RO_WEEKDAYS[day.weekday()]}"


def working_hours_label(config: AppConfig) -> str:
    hours = config.status_mapping.time.working_hours
    return f"{hours.start:%H:%M}–{hours.end:%H:%M}"


def excluded_sources_label(config: AppConfig) -> str:
    return ", ".join(config.status_mapping.sources.showroom_visit)


def week_range_label(days: tuple[date, ...]) -> str:
    first, last = days[0], days[-1]
    first_label = f"{first:%d}" if first.month == last.month else f"{first:%d.%m}"
    return f"{first_label}–{last:%d.%m}"


# Таблицы в раскладке ручного понедельничного отчёта (docs/samples/weekly-manual-report-2026-07.md):
# одни и те же строки идут в Telegram и в Excel.
def day_showroom_table(counts: DayShowroomCounts, first_header: str) -> list[TableRow]:
    header: TableRow = [first_header, *map(showroom_label, counts.showrooms), "TOTAL"]
    rows: list[TableRow] = [
        [
            short_day_label(day),
            *(counts.counts[day][showroom] for showroom in counts.showrooms),
            counts.day_total(day),
        ]
        for day in counts.days
    ]
    total: TableRow = [
        "TOTAL",
        *(counts.showroom_total(showroom) for showroom in counts.showrooms),
        counts.total,
    ]
    return [header, *rows, total]


def showroom_source_table(tables: WeeklyLeadTables) -> list[TableRow]:
    header: TableRow = ["Showroom", *map(source_label, tables.sources), "TOTAL"]
    rows: list[TableRow] = [
        [showroom_label(showroom), *by_source.values(), tables.showroom_total(showroom)]
        for showroom, by_source in tables.by_showroom_source.items()
    ]
    total: TableRow = [
        "TOTAL",
        *(tables.source_total(source) for source in tables.sources),
        tables.total,
    ]
    return [header, *rows, total]


def day_detail_table(tables: WeeklyLeadTables) -> list[TableRow]:
    rows: list[TableRow] = [["Zi / Showroom", *map(source_label, tables.sources), "TOTAL"]]
    for day, by_showroom in tables.by_day_showroom_source.items():
        rows.append([long_day_label(day)])
        for showroom, by_source in by_showroom.items():
            rows.append(
                [
                    f"   {showroom_label(showroom)}",
                    *by_source.values(),
                    tables.day_showroom_total(day, showroom),
                ]
            )
        rows.append(
            [
                f"Total zi {day:%d-%m}",
                *tables.day_source_totals(day).values(),
                tables.by_day_showroom.day_total(day),
            ]
        )
    return rows


def text_rows(table: list[TableRow]) -> list[str]:
    return [" | ".join(str(cell) for cell in row) for row in table]


def weekly_leads_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    tables = weekly_lead_tables(lead_frame, context.report_date, context.config)
    days = tables.by_day_showroom.days
    return ModuleResult(
        render(
            "weekly_leads",
            tables=tables,
            week_number=context.report_date.isocalendar().week,
            week_range=week_range_label(days),
            day_showroom_rows=text_rows(
                day_showroom_table(tables.by_day_showroom, "Zi lucrătoare")
            ),
            showroom_source_rows=text_rows(showroom_source_table(tables)),
            working_hours=working_hours_label(context.config),
            excluded_sources=excluded_sources_label(context.config),
        )
    )


def showroom_visits_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    visits = weekly_showroom_visits(lead_frame, context.report_date, context.config)
    return ModuleResult(
        render(
            "showroom_visits",
            visits=visits,
            day_showroom_rows=text_rows(day_showroom_table(visits, "Zi")),
            without_showroom=WITHOUT_SHOWROOM,
        )
    )


def weekly_funnel_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    funnel = weekly_funnel(lead_frame, context.report_date, context.config)
    return ModuleResult(render("weekly_funnel", funnel=funnel))


def loss_reasons_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    losses = weekly_loss_reasons(lead_frame, context.report_date, context.config)
    reason_config = context.config.status_mapping.categories.LOST.reasons
    reason_labels = {key: reason.label for key, reason in reason_config.items()}
    return ModuleResult(
        render(
            "loss_reasons",
            losses=losses,
            reason_labels=reason_labels,
            without_showroom=WITHOUT_SHOWROOM,
        )
    )


def week_over_week_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    change = week_over_week(lead_frame, context.week_ago, context.report_date, context.config)
    return ModuleResult(
        render(
            "week_over_week",
            change=change,
            leads_change=relative_change(change.leads, change.leads_previous),
            showroom_visits_change=relative_change(
                change.showroom_visits, change.showroom_visits_previous
            ),
            contracts_change=relative_change(change.contracts, change.contracts_previous),
        )
    )
