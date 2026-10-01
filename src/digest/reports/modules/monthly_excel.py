from io import BytesIO
from typing import Any

import pandas as pd
import xlsxwriter

from digest.config import KPI_NAMES
from digest.metrics.kpi import kpis_from
from digest.metrics.monthly import (
    REPEAT_BY_CONTACT,
    REPEAT_BY_SOURCE,
    CohortRow,
    DealCycle,
    month_window,
    monthly_client_rows,
    monthly_cohort_conversion,
    monthly_funnel,
    monthly_lead_rows,
    monthly_loss_reasons,
)
from digest.reports.charts import chart_labels
from digest.reports.context import ModuleResult, ReportContext, ReportDocument
from digest.reports.modules.monthly import (
    loss_reason_labels,
    manager_cockpit_rows,
    month_file_suffix,
    target_labels,
)
from digest.reports.modules.weekly import showroom_label
from digest.reports.modules.weekly_excel import LEAD_SHEET_COLUMNS, sheet_text, write_lead_sheet
from digest.reports.render import count_noun, render

COUNT_HEADERS = (
    ("leads", "Lead-uri"),
    ("useful", "Utile"),
    ("offers", "Oferte"),
    ("clienti", "Clienți"),
)
FUNNEL_KPI_NAMES = ("scr", "l2o", "o2c")
# O2C = Clienți / Oferte (docs/kpi-definitions.md): ступень статуса «Clienți», а не договор.
KPI_HEADER_WORDS = {"o2c": "Ofertă→Client"}
NAME_COLUMN_WIDTH = 22
VALUE_COLUMN_WIDTH = 10
MISSING_VALUE = "—"
TARGET_MET_COLOR = "#008300"
TARGET_MISSED_COLOR = "#c62828"


def kpi_header(kpi_name: str) -> str:
    words = KPI_HEADER_WORDS.get(kpi_name)
    return kpi_name.upper() if words is None else f"{kpi_name.upper()} ({words})"


def write_share(
    worksheet: Any, row: int, column: int, value: float | None, cell_format: Any
) -> None:
    if value is None:
        worksheet.write_string(row, column, MISSING_VALUE)
    else:
        worksheet.write_number(row, column, value, cell_format)


def write_clienti_note(worksheet: Any, last_table_row: int) -> None:
    worksheet.write(last_table_row + 2, 0, chart_labels().clienti_note)


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
            *(kpi_header(name) for name in KPI_NAMES),
        ],
    )
    worksheet.write(1, 0, "Țintă")
    worksheet.write_row(1, first_kpi_column, [targets[name] for name in KPI_NAMES])
    rows = manager_cockpit_rows(lead_frame, context)
    for row_index, row in enumerate(rows, start=2):
        worksheet.write_row(
            row_index,
            0,
            [
                row["name"],
                showroom_label(row["showroom"]),
                *(int(row[name]) for name, _ in COUNT_HEADERS),
            ],
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
            *(kpi_header(name) for name in FUNNEL_KPI_NAMES),
        ],
    )
    rows = [
        (showroom_label(showroom), funnel.by_showroom[showroom])
        for showroom in funnel.showrooms_with_leads
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
    labels = loss_reason_labels(context.config)
    month_labels = chart_labels()
    worksheet.write_row(
        0,
        0,
        [
            "Motiv",
            month_labels.month_name(losses.month),
            "Pondere",
            month_labels.month_name(losses.previous_month),
            "Variație",
        ],
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


def write_days(worksheet: Any, row: int, column: int, days: float | None) -> None:
    if days is None:
        worksheet.write_string(row, column, MISSING_VALUE)
    else:
        worksheet.write_number(row, column, round(days, 1))


def write_cohort_row(
    worksheet: Any,
    row_index: int,
    label: str,
    showroom: str,
    row: CohortRow,
    buckets: list[int],
    formats: dict[str, Any],
) -> None:
    counts = row.counts
    worksheet.write_row(
        row_index, 0, [label, showroom, counts.leads, counts.useful, counts.clienti]
    )
    write_share(worksheet, row_index, 5, row.conversion, formats["percent"])
    for offset, bucket in enumerate(buckets):
        write_share(worksheet, row_index, 6 + offset, row.share_within(bucket), formats["percent"])
    write_days(worksheet, row_index, 6 + len(buckets), row.median_days)
    worksheet.write(
        row_index,
        7 + len(buckets),
        sheet_text("in_progress_yes" if row.in_progress else "in_progress_no"),
    )


def write_cycle_row(
    worksheet: Any, row_index: int, showroom: str, cycle: DealCycle, formats: dict[str, Any]
) -> None:
    worksheet.write_row(row_index, 0, [showroom, cycle.contracts])
    write_days(worksheet, row_index, 2, cycle.median_days)
    write_days(worksheet, row_index, 3, cycle.p75_days)
    write_share(worksheet, row_index, 4, cycle.share_within_fast, formats["percent"])


def write_cohort_sheet(
    workbook: Any, lead_frame: pd.DataFrame, context: ReportContext, formats: dict[str, Any]
) -> None:
    worksheet = workbook.add_worksheet(sheet_text("sheet_cohorts"))
    conversion = monthly_cohort_conversion(lead_frame, context.report_date, context.config)
    params = context.config.modules.cohort_conversion_params
    buckets = params.age_buckets_days
    month_labels = chart_labels()
    worksheet.write_row(
        0,
        0,
        [
            "Luna",
            "Showroom",
            "Lead-uri",
            "Utile",
            "Clienți",
            "%",
            *(f"≤{count_noun(bucket, 'zi', 'zile')}" for bucket in buckets),
            "Mediană zile",
            sheet_text("column_in_progress"),
        ],
    )
    row_index = 1
    for index, company in enumerate(conversion.cohorts):
        label = month_labels.month_label(company.month)
        write_cohort_row(worksheet, row_index, label, "Total", company, buckets, formats)
        row_index += 1
        for showroom, cohorts in conversion.cohorts_by_showroom.items():
            if cohorts[index].counts.leads:
                write_cohort_row(
                    worksheet,
                    row_index,
                    label,
                    showroom_label(showroom),
                    cohorts[index],
                    buckets,
                    formats,
                )
                row_index += 1
    worksheet.write(row_index + 1, 0, sheet_text("cohort_note", longest_bucket=buckets[-1]))
    cycle_header_row = row_index + 3
    worksheet.write(
        cycle_header_row,
        0,
        sheet_text("cycle_title", month=month_labels.month_label(conversion.report_month)),
    )
    worksheet.write_row(
        cycle_header_row + 1,
        0,
        [
            "Showroom",
            "Contracte",
            "Mediană zile",
            "P75 zile",
            f"≤{count_noun(params.fast_cycle_days, 'zi', 'zile')}",
        ],
    )
    cycle_rows = [
        (showroom_label(showroom), cycle)
        for showroom, cycle in conversion.cycle_by_showroom.items()
        if cycle.contracts
    ]
    for offset, (showroom, cycle) in enumerate(
        [("Total", conversion.cycle), *cycle_rows], start=cycle_header_row + 2
    ):
        write_cycle_row(worksheet, offset, showroom, cycle, formats)
    worksheet.set_column(0, 1, NAME_COLUMN_WIDTH)
    worksheet.set_column(2, 7 + len(buckets), VALUE_COLUMN_WIDTH)


def monthly_workbook(lead_frame: pd.DataFrame, context: ReportContext) -> bytes:
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})
    formats = {
        "percent": workbook.add_format({"num_format": "0.0%"}),
        "met": workbook.add_format({"num_format": "0.0%", "font_color": TARGET_MET_COLOR}),
        "missed": workbook.add_format({"num_format": "0.0%", "font_color": TARGET_MISSED_COLOR}),
    }
    write_manager_sheet(workbook, lead_frame, context, formats)
    write_funnel_sheet(workbook, lead_frame, context, formats)
    write_loss_sheet(workbook, lead_frame, context, formats)
    write_cohort_sheet(workbook, lead_frame, context, formats)
    write_lead_sheet(
        workbook,
        "Lead-uri luna",
        "Zi",
        monthly_lead_rows(lead_frame, context.report_date, context.config),
        context.config,
        context.lead_links,
    )
    window = month_window(context.report_date, context.config.status_mapping.time)
    workbook.get_worksheet_by_name("Lead-uri luna").write(
        0,
        len(LEAD_SHEET_COLUMNS) + 1,
        f"Luna: lead-uri create {window.start:%d.%m.%Y %H:%M} → {window.end:%d.%m.%Y %H:%M}",
    )
    client_rows = monthly_client_rows(lead_frame, context.report_date, context.config)
    write_lead_sheet(
        workbook,
        sheet_text("sheet_month_clients"),
        sheet_text("column_contract_day"),
        client_rows.assign(
            is_repeat=client_rows["is_repeat"].map(
                {True: sheet_text("repeat_yes"), False: sheet_text("repeat_no")}
            ),
            # Причина по источнику подписана самим источником, как в mefi («Client Fidel»).
            repeat_reason=client_rows["repeat_reason"]
            .map({REPEAT_BY_CONTACT: sheet_text("repeat_by_contact")})
            .where(client_rows["repeat_reason"].ne(REPEAT_BY_SOURCE), client_rows["source_name"]),
        ),
        context.config,
        context.lead_links,
        (
            ("is_repeat", sheet_text("column_repeat")),
            ("repeat_reason", sheet_text("column_repeat_reason")),
        ),
    )
    workbook.close()
    return output.getvalue()


def monthly_excel_attachment_report(
    lead_frame: pd.DataFrame, context: ReportContext
) -> ModuleResult:
    filename = f"{context.tenant_id}_{month_file_suffix(context.report_date)}.xlsx"
    return ModuleResult(
        render("monthly_excel_attachment", filename=filename),
        document=ReportDocument(filename, monthly_workbook(lead_frame, context)),
    )
