from io import BytesIO
from typing import Any

import pandas as pd
import xlsxwriter

from digest.config import AppConfig
from digest.metrics.weekly import (
    weekly_lead_rows,
    weekly_lead_tables,
    weekly_showroom_visit_rows,
)
from digest.reports.context import ModuleResult, ReportContext, ReportDocument
from digest.reports.lead_links import LeadLinks
from digest.reports.modules.weekly import (
    TableRow,
    day_detail_table,
    day_showroom_table,
    excluded_sources_label,
    showroom_source_table,
    week_range_label,
    working_hours_label,
)
from digest.reports.render import render, text

# Колонки листов «Lead-uri» и «Vizite»: имя колонки ячеек и подпись. Ячейки строятся только из
# LEAD_ROW_COLUMNS metrics/weekly.py, где нет данных клиента (инвариант 7).
LEAD_SHEET_COLUMNS = (
    ("lead_id", "ID"),
    ("created_at", "Creat"),
    ("day", "Zi lucrătoare"),
    ("showroom", "Showroom"),
    ("source_name", "Sursa"),
    ("status_name", "Status"),
    ("ofertat", "Ofertat"),
    ("consultant", "Consilier"),
)
LEAD_SHEET_COLUMN_WIDTH = 16


def write_table(worksheet: Any, first_row: int, table: list[TableRow]) -> None:
    for offset, row in enumerate(table):
        worksheet.write_row(first_row + offset, 0, row)


def lead_sheet_cells(
    rows: pd.DataFrame, config: AppConfig, extra_columns: tuple[str, ...] = ()
) -> pd.DataFrame:
    known_manager_ids = {manager.id for manager in config.managers.managers}
    ofertat_field = config.status_mapping.custom_fields.ofertat
    assigned_to_id = rows["assigned_to_id"]
    consultant = rows["assigned_to_name"].where(
        assigned_to_id.isin(known_manager_ids), "id " + assigned_to_id.astype("string")
    )
    ofertat = rows["ofertat"].map(
        {True: ofertat_field.ofertat_yes, False: ofertat_field.ofertat_no}
    )
    cells = rows.assign(
        # Excel не хранит таймзону: пишем время по Бухаресту.
        created_at=rows["created_at"].dt.tz_localize(None),
        ofertat=ofertat,
        consultant=consultant,
    )
    return cells[[*(name for name, _ in LEAD_SHEET_COLUMNS), *extra_columns]]


def write_lead_sheet(
    workbook: Any,
    name: str,
    day_header: str,
    rows: pd.DataFrame,
    config: AppConfig,
    lead_links: LeadLinks,
    extra_columns: tuple[tuple[str, str], ...] = (),
) -> None:
    worksheet = workbook.add_worksheet(name)
    cell_formats = {
        "created_at": workbook.add_format({"num_format": "yyyy-mm-dd hh:mm"}),
        "day": workbook.add_format({"num_format": "yyyy-mm-dd"}),
    }
    columns = (*LEAD_SHEET_COLUMNS, *extra_columns)
    headers = {**dict(columns), "day": day_header}
    worksheet.write_row(0, 0, list(headers.values()))
    cells = lead_sheet_cells(rows, config, tuple(name for name, _ in extra_columns))
    for row_index, row in enumerate(cells.to_dict("records"), start=1):
        for column_index, (column, _) in enumerate(columns):
            value = row[column]
            if column == "lead_id":
                # Инвариант 7: столбец id во вложении разрешён; ссылка открывает лид в mefi.
                lead_id = int(value)
                worksheet.write_url(
                    row_index, column_index, lead_links.url(lead_id), string=str(lead_id)
                )
            elif column in cell_formats:
                worksheet.write_datetime(row_index, column_index, value, cell_formats[column])
            else:
                worksheet.write(row_index, column_index, None if pd.isna(value) else value)
    worksheet.set_column(0, len(columns) - 1, LEAD_SHEET_COLUMN_WIDTH)


def sheet_text(macro_name: str, **values: Any) -> str:
    # Ячейка Excel не HTML: снимаем экранирование autoescape, иначе «&» из конфига станет «&amp;».
    return text(macro_name, **values).unescape()


def weekly_workbook(lead_frame: pd.DataFrame, context: ReportContext) -> bytes:
    report_date, config = context.report_date, context.config
    tables = weekly_lead_tables(lead_frame, report_date, config)
    week_range = week_range_label(tables.by_day_showroom.days)
    sources = excluded_sources_label(config)
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})

    summary = workbook.add_worksheet(sheet_text("sheet_day_showroom"))
    summary.write(0, 0, sheet_text("day_showroom_title", week_range=week_range, sources=sources))
    summary.write(1, 0, sheet_text("working_hours_rule", hours=working_hours_label(config)))
    write_table(summary, 2, day_showroom_table(tables.by_day_showroom, "Zi lucrătoare"))

    detail = workbook.add_worksheet(sheet_text("sheet_day_showroom_source"))
    detail.write(
        0, 0, sheet_text("day_showroom_source_title", week_range=week_range, sources=sources)
    )
    detail.write(1, 0, sheet_text("day_showroom_source_hint"))
    write_table(detail, 2, day_detail_table(tables))

    by_source = workbook.add_worksheet(sheet_text("sheet_showroom_source"))
    by_source.write(
        0, 0, sheet_text("showroom_source_title", week_range=week_range, sources=sources)
    )
    by_source.write(1, 0, sheet_text("showroom_source_hint", week_range=week_range))
    write_table(by_source, 2, showroom_source_table(tables))

    write_lead_sheet(
        workbook,
        sheet_text("sheet_leads"),
        "Zi lucrătoare",
        weekly_lead_rows(lead_frame, report_date, config),
        config,
        context.lead_links,
    )
    # День визита это день ежедневного окна 19:00 → 19:00, а не рабочий день лида.
    write_lead_sheet(
        workbook,
        sheet_text("sheet_visits"),
        "Zi",
        weekly_showroom_visit_rows(lead_frame, report_date, config),
        config,
        context.lead_links,
    )
    workbook.close()
    return output.getvalue()


def excel_attachment_report(lead_frame: pd.DataFrame, context: ReportContext) -> ModuleResult:
    iso_year, iso_week, _ = context.report_date.isocalendar()
    filename = f"{context.tenant_id}_sapt{iso_week:02d}_{iso_year}.xlsx"
    return ModuleResult(
        render("excel_attachment", filename=filename),
        document=ReportDocument(filename, weekly_workbook(lead_frame, context)),
    )
