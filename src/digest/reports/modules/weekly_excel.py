from io import BytesIO
from typing import Any

import pandas as pd
import xlsxwriter

from digest.metrics.weekly import (
    weekly_lead_rows,
    weekly_lead_tables,
    weekly_showroom_visit_rows,
)
from digest.reports.context import ModuleResult, ReportContext, ReportDocument
from digest.reports.modules.weekly import (
    TableRow,
    day_detail_table,
    day_showroom_table,
    showroom_source_table,
    week_range_label,
)
from digest.reports.render import render

LEAD_SHEET_HEADER = (
    "ID",
    "Creat",
    "Zi lucrătoare",
    "Showroom",
    "Sursa",
    "Status",
    "Ofertat",
    "Consilier",
)
LEAD_SHEET_TEXT_COLUMNS = ("showroom", "source_name", "status_name", "ofertat", "consultant")
WORKING_HOURS_RULE = (
    "Правило: рабочие часы 10:00–19:00. Лиды в этом окне засчитаны в текущий день; лиды вне "
    "окна перенесены на следующий календарный день (продавцы обрабатывают их только в рабочее "
    "время)."
)


def write_table(worksheet: Any, first_row: int, table: list[TableRow]) -> None:
    for offset, row in enumerate(table):
        worksheet.write_row(first_row + offset, 0, row)


def write_lead_sheet(workbook: Any, name: str, rows: pd.DataFrame) -> None:
    worksheet = workbook.add_worksheet(name)
    date_time_format = workbook.add_format({"num_format": "yyyy-mm-dd hh:mm"})
    date_format = workbook.add_format({"num_format": "yyyy-mm-dd"})
    worksheet.write_row(0, 0, LEAD_SHEET_HEADER)
    for index, row in enumerate(rows.to_dict("records"), start=1):
        worksheet.write_number(index, 0, row["lead_id"])
        worksheet.write_datetime(index, 1, row["created_at"].to_pydatetime(), date_time_format)
        worksheet.write_datetime(index, 2, row["day"], date_format)
        for column, name in enumerate(LEAD_SHEET_TEXT_COLUMNS, start=3):
            value = row[name]
            worksheet.write(index, column, None if pd.isna(value) else value)
    worksheet.set_column(0, len(LEAD_SHEET_HEADER) - 1, 16)


def weekly_workbook(lead_frame: pd.DataFrame, context: ReportContext) -> bytes:
    report_date, config = context.report_date, context.config
    tables = weekly_lead_tables(lead_frame, report_date, config)
    week_range = week_range_label(tables.by_day_showroom.days)
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})

    summary = workbook.add_worksheet("Свод день-шоурум")
    summary.write(0, 0, f"Сводка {week_range} (БЕЗ Sursa=Showroom): лиды по рабочим дням × шоурум")
    summary.write(1, 0, WORKING_HOURS_RULE)
    write_table(summary, 2, day_showroom_table(tables.by_day_showroom, "Zi lucrătoare"))

    detail = workbook.add_worksheet("По дням шоурум-источник")
    detail.write(
        0,
        0,
        f"Детализация по дням {week_range} (БЕЗ Sursa=Showroom): шоурум × источник, "
        "с итогом после каждого дня",
    )
    detail.write(1, 0, "После каждого рабочего дня строка «Total zi».")
    write_table(detail, 2, day_detail_table(tables))

    by_source = workbook.add_worksheet("Шоурум-источник итог")
    by_source.write(0, 0, f"Итог {week_range} (БЕЗ Sursa=Showroom): шоурум × источник")
    by_source.write(1, 0, f"Откуда приходят лиды в каждый шоурум (весь период {week_range}).")
    write_table(by_source, 2, showroom_source_table(tables))

    write_lead_sheet(workbook, "Lead-uri", weekly_lead_rows(lead_frame, report_date, config))
    write_lead_sheet(
        workbook, "Vizite", weekly_showroom_visit_rows(lead_frame, report_date, config)
    )
    workbook.close()
    return output.getvalue()


def excel_attachment_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    iso_year, iso_week, _ = context.report_date.isocalendar()
    filename = f"sofabelle_sapt{iso_week:02d}_{iso_year}.xlsx"
    return ModuleResult(
        render("excel_attachment", context.language, filename=filename),
        document=ReportDocument(filename, weekly_workbook(lead_frame, context)),
    )
