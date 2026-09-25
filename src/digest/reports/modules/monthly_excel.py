from io import BytesIO
from typing import Any

import pandas as pd
import xlsxwriter

from digest.config import KPI_NAMES
from digest.metrics.kpi import kpis_from
from digest.metrics.monthly import monthly_funnel, monthly_lead_rows, monthly_loss_reasons
from digest.reports.charts import chart_labels
from digest.reports.context import ModuleResult, ReportContext, ReportDocument
from digest.reports.modules.monthly import (
    loss_reason_labels,
    manager_cockpit_rows,
    month_file_suffix,
    showrooms_with_leads,
    target_labels,
)
from digest.reports.modules.weekly import showroom_label
from digest.reports.modules.weekly_excel import write_lead_sheet
from digest.reports.render import render

COUNT_HEADERS = (
    ("leads", "Lead-uri"),
    ("useful", "Utile"),
    ("offers", "Oferte"),
    ("clienti", "Clienți"),
)
FUNNEL_KPI_NAMES = ("scr", "l2o", "o2c")
NAME_COLUMN_WIDTH = 22
VALUE_COLUMN_WIDTH = 10
MISSING_VALUE = "—"


def write_share(
    worksheet: Any, row: int, column: int, value: float | None, cell_format: Any
) -> None:
    if value is None:
        worksheet.write_string(row, column, MISSING_VALUE)
    else:
        worksheet.write_number(row, column, value, cell_format)


def write_clienti_note(worksheet: Any, last_table_row: int) -> None:
    worksheet.write(last_table_row + 2, 0, chart_labels("ro").clienti_note)


def write_manager_sheet(
    workbook: Any, lead_frame: pd.DataFrame, context: ReportContext, formats: dict[str, Any]
) -> None:
    worksheet = workbook.add_worksheet("KPI consilieri")
    targets = target_labels(context.config)
    first_kpi_column = 2 + len(COUNT_HEADERS)
    worksheet.write_row(
        0,
        0,
        [
            "Consilier",
            "Showroom",
            *(label for _, label in COUNT_HEADERS),
            *(name.upper() for name in KPI_NAMES),
        ],
    )
    worksheet.write(1, 0, "Țintă")
    worksheet.write_row(1, first_kpi_column, [targets[name] for name in KPI_NAMES])
    rows = manager_cockpit_rows(lead_frame, context)
    for row_index, row in enumerate(rows, start=2):
        worksheet.write_row(
            row_index,
            0,
            [row["name"], row["showroom"] or "", *(int(row[name]) for name, _ in COUNT_HEADERS)],
        )
        for offset, kpi_name in enumerate(KPI_NAMES):
            meets_target = row[f"{kpi_name}_meets_target"]
            cell_format = {True: formats["met"], False: formats["missed"]}.get(
                meets_target, formats["percent"]
            )
            write_share(worksheet, row_index, first_kpi_column + offset, row[kpi_name], cell_format)
    write_clienti_note(worksheet, 1 + len(rows))
    worksheet.set_column(0, 0, NAME_COLUMN_WIDTH)
    worksheet.set_column(1, first_kpi_column + len(KPI_NAMES) - 1, VALUE_COLUMN_WIDTH)


def write_funnel_sheet(
    workbook: Any, lead_frame: pd.DataFrame, context: ReportContext, formats: dict[str, Any]
) -> None:
    worksheet = workbook.add_worksheet("Funnel showroom")
    funnel = monthly_funnel(lead_frame, context.report_date, context.config)
    worksheet.write_row(
        0,
        0,
        [
            "Showroom",
            *(label for _, label in COUNT_HEADERS),
            *(name.upper() for name in FUNNEL_KPI_NAMES),
        ],
    )
    rows = [
        (showroom_label(showroom), funnel.by_showroom[showroom])
        for showroom in showrooms_with_leads(funnel)
    ]
    rows.append(("Total", funnel.company))
    for row_index, (label, counts) in enumerate(rows, start=1):
        worksheet.write_row(
            row_index, 0, [label, *(getattr(counts, name) for name, _ in COUNT_HEADERS)]
        )
        kpis = kpis_from(counts)
        for offset, kpi_name in enumerate(FUNNEL_KPI_NAMES):
            write_share(
                worksheet,
                row_index,
                1 + len(COUNT_HEADERS) + offset,
                getattr(kpis, kpi_name),
                formats["percent"],
            )
    write_clienti_note(worksheet, len(rows))
    worksheet.set_column(0, 0, NAME_COLUMN_WIDTH)
    worksheet.set_column(1, len(COUNT_HEADERS) + len(FUNNEL_KPI_NAMES), VALUE_COLUMN_WIDTH)


def write_loss_sheet(
    workbook: Any, lead_frame: pd.DataFrame, context: ReportContext, formats: dict[str, Any]
) -> None:
    worksheet = workbook.add_worksheet("Motive pierdere")
    losses = monthly_loss_reasons(lead_frame, context.report_date, context.config)
    labels = loss_reason_labels(context)
    months = chart_labels("ro").months
    month = context.report_date.month
    worksheet.write_row(
        0, 0, ["Motiv", months[month - 1], "Pondere", months[(month - 2) % 12], "Variație"]
    )
    rows = [
        (
            labels[reason],
            losses.current.reason_total(reason),
            losses.reason_share(reason),
            losses.previous.reason_total(reason),
            losses.reason_change(reason),
        )
        for reason in losses.reasons_by_count
    ]
    rows.append(
        (
            "Total",
            losses.current.total,
            losses.total_share,
            losses.previous.total,
            losses.total_change,
        )
    )
    for row_index, (label, current, share, previous, change) in enumerate(rows, start=1):
        worksheet.write_row(row_index, 0, [label, current])
        write_share(worksheet, row_index, 2, share, formats["percent"])
        worksheet.write(row_index, 3, previous)
        write_share(worksheet, row_index, 4, change, formats["percent"])
    worksheet.set_column(0, 0, NAME_COLUMN_WIDTH)
    worksheet.set_column(1, 4, VALUE_COLUMN_WIDTH)


def monthly_workbook(lead_frame: pd.DataFrame, context: ReportContext) -> bytes:
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    formats = {
        "percent": workbook.add_format({"num_format": "0.0%"}),
        "met": workbook.add_format({"num_format": "0.0%", "font_color": "#008300"}),
        "missed": workbook.add_format({"num_format": "0.0%", "font_color": "#c62828"}),
    }
    write_manager_sheet(workbook, lead_frame, context, formats)
    write_funnel_sheet(workbook, lead_frame, context, formats)
    write_loss_sheet(workbook, lead_frame, context, formats)
    write_lead_sheet(
        workbook,
        "Lead-uri luna",
        "Zi",
        monthly_lead_rows(lead_frame, context.report_date, context.config),
        context.config,
    )
    workbook.close()
    return output.getvalue()


def monthly_excel_attachment_report(
    lead_frame: pd.DataFrame, context: ReportContext
) -> ModuleResult:
    filename = f"{context.tenant_id}_{month_file_suffix(context.report_date)}.xlsx"
    return ModuleResult(
        render("monthly_excel_attachment", context.language, filename=filename),
        document=ReportDocument(filename, monthly_workbook(lead_frame, context)),
    )
