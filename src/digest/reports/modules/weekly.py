from datetime import date

import pandas as pd

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
        [
            showroom_label(showroom),
            *by_source.values(),
            sum(by_source.values()),
        ]
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
                [f"   {showroom_label(showroom)}", *by_source.values(), sum(by_source.values())]
            )
        day_by_source = [
            sum(by_source[source] for by_source in by_showroom.values())
            for source in tables.sources
        ]
        rows.append([f"Total zi {day:%d-%m}", *day_by_source, sum(day_by_source)])
    return rows


def text_rows(table: list[TableRow]) -> list[str]:
    return [" | ".join(str(cell) for cell in row) for row in table]


def weekly_leads_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    tables = weekly_lead_tables(lead_frame, context.report_date, context.config)
    days = tables.by_day_showroom.days
    return ModuleResult(
        render(
            "weekly_leads",
            context.language,
            tables=tables,
            week_number=context.report_date.isocalendar().week,
            week_range=week_range_label(days),
            day_showroom_rows=text_rows(
                day_showroom_table(tables.by_day_showroom, "Zi lucrătoare")
            ),
            showroom_source_rows=text_rows(showroom_source_table(tables)),
        )
    )


def showroom_visits_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    visits = weekly_showroom_visits(lead_frame, context.report_date, context.config)
    return ModuleResult(
        render(
            "showroom_visits",
            context.language,
            visits=visits,
            day_showroom_rows=text_rows(day_showroom_table(visits, "Zi")),
        )
    )


def weekly_funnel_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    funnel = weekly_funnel(lead_frame, context.report_date, context.config)
    return ModuleResult(render("weekly_funnel", context.language, funnel=funnel))


def loss_reasons_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    losses = weekly_loss_reasons(lead_frame, context.report_date, context.config)
    reasons_by_count = sorted(
        (reason for reason in losses.reasons if losses.reason_total(reason)),
        key=lambda reason: -losses.reason_total(reason),
    )
    return ModuleResult(
        render("loss_reasons", context.language, losses=losses, reasons_by_count=reasons_by_count)
    )


def week_over_week_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    change = week_over_week(lead_frame, context.week_ago, context.report_date, context.config)
    return ModuleResult(
        render(
            "week_over_week",
            context.language,
            change=change,
            leads_change=relative_change(change.leads, change.leads_previous),
            showroom_visits_change=relative_change(
                change.showroom_visits, change.showroom_visits_previous
            ),
            contracts_change=relative_change(change.contracts, change.contracts_previous),
        )
    )
