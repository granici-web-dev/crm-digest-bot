import re
import unicodedata
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from io import BytesIO
from typing import Literal
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

from sqlalchemy import Table, bindparam, func, or_, select, text
from sqlalchemy.dialects.postgresql import ARRAY, TEXT

from digest.db.schema import client_snapshots, lead_snapshots
from digest.reports.lead_links import LeadLinks
from digest.reports.periods import ReportLevel, report_period
from digest.reports.runner import (
    BuiltReport,
    ReportReader,
    load_report_inputs,
    render_report,
    report_snapshot_date,
    select_modules,
)

ViolationKind = Literal["phone", "email", "foreign_link", "too_many_links"]

LINK = re.compile(r"<a\s[^>]*href=\"([^\"]*)\"[^>]*>", re.IGNORECASE)
BOLD_HEADER = re.compile(r"<b>.*?</b>", re.DOTALL)
TAG = re.compile(r"<[^>]+>")
# Даты и время (28.09.2026 19:00 в тексте, 21-09-2026 во вложениях) без вырезания склеились бы
# в «семь цифр подряд».
DAY = r"(?:0?[1-9]|[12]\d|3[01])"
MONTH = r"(?:0?[1-9]|1[0-2])"
DATE_OR_TIME = re.compile(
    rf"\b\d{{4}}-\d{{2}}-\d{{2}}\b|\b{DAY}[.\-/]{MONTH}[.\-/]\d{{4}}\b|\b{DAY}\.{MONTH}\b"
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\b"
)
# Разделители внутри телефона: пробелы (включая NBSP), дефис, точка, скобки. Не больше двух
# подряд, иначе колонки таблиц в <code> («12   34») читались бы как один номер.
PHONE = re.compile(r"\+?\d(?:[\s\-.()]{0,2}\d){6,}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
XLSX_NAMESPACE = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
INTEGER_CELL = re.compile(r"-?\d{7,}")


@dataclass(frozen=True)
class PrivacyViolation:
    kind: ViolationKind
    fragment: str


def mask_digits(value: str) -> str:
    return re.sub(r"\d", "•", value)


def without_invisible(value: str) -> str:
    # Cf (zero-width, bidi) невидимы и разорвали бы цифры телефона мимо проверки.
    return "".join(character for character in value if unicodedata.category(character) != "Cf")


def contact_violations(value: str) -> list[PrivacyViolation]:
    visible = DATE_OR_TIME.sub(" ", without_invisible(value))
    return [
        PrivacyViolation("phone", mask_digits(match.group())) for match in PHONE.finditer(visible)
    ] + [
        PrivacyViolation("email", "•••@" + match.group().split("@", 1)[1])
        for match in EMAIL.finditer(visible)
    ]


def text_blocks(value: str) -> list[str]:
    # Блок для лимита ссылок: текст между соседними заголовками <b> (shape acceptance, решение 2).
    starts = [0, *(match.start() for match in BOLD_HEADER.finditer(value))]
    return [value[start:end] for start, end in zip(starts, [*starts[1:], len(value)], strict=True)]


def privacy_violations(value: str, lead_links: LeadLinks) -> list[PrivacyViolation]:
    violations: list[PrivacyViolation] = []
    allowed_link = re.compile(
        re.escape(lead_links.web_origin)
        + re.escape(lead_links.path).replace(re.escape("{lead_id}"), r"\d+")
    )
    for href in LINK.findall(value):
        if not allowed_link.fullmatch(href):
            violations.append(PrivacyViolation("foreign_link", mask_digits(href)))
    for block in text_blocks(value):
        link_count = len(LINK.findall(block))
        if link_count > lead_links.limit:
            violations.append(
                PrivacyViolation("too_many_links", f"{link_count} > {lead_links.limit}")
            )
    return violations + contact_violations(TAG.sub(" | ", value))


def xlsx_texts(content: bytes) -> Iterator[str]:
    with zipfile.ZipFile(BytesIO(content)) as workbook:
        names = workbook.namelist()
        if "xl/sharedStrings.xml" in names:
            shared = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
            for item in shared.iterfind("x:si", XLSX_NAMESPACE):
                yield "".join(item.itertext())
        for name in names:
            if not (name.startswith("xl/worksheets/") and name.endswith(".xml")):
                continue
            sheet = ElementTree.fromstring(workbook.read(name))
            for cell in sheet.iterfind(".//x:c", XLSX_NAMESPACE):
                cell_type = cell.get("t")
                if cell_type == "s":
                    continue
                if cell_type == "inlineStr":
                    yield "".join(cell.itertext())
                    continue
                value = cell.findtext("x:v", default="", namespaces=XLSX_NAMESPACE)
                # Число из 7+ цифр это телефон, сохранённый числом; дроби и даты Excel пропускаем.
                if cell_type == "str" or INTEGER_CELL.fullmatch(value):
                    yield value


def xlsx_violations(content: bytes) -> list[PrivacyViolation]:
    return [violation for value in xlsx_texts(content) for violation in contact_violations(value)]


@dataclass(frozen=True)
class AuditFinding:
    report_date: date
    level: ReportLevel
    module_id: str
    violation: PrivacyViolation


@dataclass(frozen=True)
class RawFinding:
    table: str
    check: str
    rows: int


@dataclass(frozen=True)
class PrivacyAudit:
    checked_reports: int
    dates_without_snapshot: list[date]
    findings: list[AuditFinding]
    raw_findings: list[RawFinding]


def audit_levels(report_date: date) -> list[ReportLevel]:
    levels: list[ReportLevel] = ["daily"]
    if report_date.weekday() == 0:
        levels.append("weekly")
    if report_date.day == 1:
        levels.append("monthly")
    return levels


def report_findings(
    report: BuiltReport, report_date: date, level: ReportLevel, lead_links: LeadLinks
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    outer_text = report.text
    for block in report.blocks:
        if block.text is None:
            continue
        outer_text = outer_text.replace(str(block.text), "", 1)
        findings += [
            AuditFinding(report_date, level, block.module_id, violation)
            for violation in privacy_violations(str(block.text), lead_links)
        ]
    findings += [
        AuditFinding(report_date, level, "header", violation)
        for violation in privacy_violations(outer_text, lead_links)
    ]
    for document in report.documents:
        if document.filename.endswith(".xlsx"):
            findings += [
                AuditFinding(report_date, level, document.filename, violation)
                for violation in xlsx_violations(document.content)
            ]
    return findings


async def raw_findings(reader: ReportReader, snapshot_dates: set[date]) -> list[RawFinding]:
    status_mapping = reader.config.status_mapping
    checks: list[tuple[Table, list[str], frozenset[int]]] = [
        (lead_snapshots, status_mapping.raw_strip, status_mapping.raw_custom_fields.keep),
        (
            client_snapshots,
            status_mapping.clients.raw_strip,
            status_mapping.clients.raw_custom_fields.keep,
        ),
    ]
    findings: list[RawFinding] = []
    async with reader.engine.connect() as connection:
        for table, strip_paths, keep_field_ids in checks:
            scope = (table.c.tenant_id == reader.tenant_id) & table.c.snapshot_date.in_(
                snapshot_dates
            )
            stripped_present = or_(
                *(
                    table.c.raw.op("#>")(
                        bindparam(f"path_{index}", path.split("."), type_=ARRAY(TEXT))
                    ).is_not(None)
                    for index, path in enumerate(strip_paths)
                )
            )
            foreign_custom_field = text(
                "EXISTS (SELECT 1 FROM jsonb_array_elements(CASE "
                f"jsonb_typeof({table.name}.raw->'custom_fields') WHEN 'array' "
                f"THEN {table.name}.raw->'custom_fields' ELSE '[]'::jsonb END) AS field "
                "WHERE NOT (field->>'field_id' = ANY(:keep_field_ids)))"
            ).bindparams(
                bindparam(
                    "keep_field_ids",
                    [str(field_id) for field_id in sorted(keep_field_ids)],
                    type_=ARRAY(TEXT),
                )
            )
            for check, condition in (
                ("ключи raw_strip", stripped_present),
                ("custom_fields вне keep", foreign_custom_field),
            ):
                rows = await connection.scalar(
                    select(func.count()).select_from(table).where(scope, condition)
                )
                if rows:
                    findings.append(RawFinding(table.name, check, rows))
    return findings


async def audit_privacy(reader: ReportReader, end: date, days: int) -> PrivacyAudit:
    time_settings = reader.config.status_mapping.time
    timezone = ZoneInfo(time_settings.timezone)
    checked_reports = 0
    dates_without_snapshot: list[date] = []
    findings: list[AuditFinding] = []
    snapshot_dates: set[date] = set()
    for offset in range(days - 1, -1, -1):
        report_date = end - timedelta(days=offset)
        # Время как у ручного отчёта (python -m digest report): конец ежедневного окна.
        now = datetime.combine(report_date, time_settings.daily_window_end, tzinfo=timezone)
        for level in audit_levels(report_date):
            period = report_period(level, now, time_settings)
            snapshot_date = report_snapshot_date(period, timezone)
            selection = await select_modules(reader, level)
            module_ids = {module_id for module_id, _ in selection.runnable}
            inputs = await load_report_inputs(reader, level, snapshot_date, module_ids)
            if inputs is None:
                if snapshot_date not in dates_without_snapshot:
                    dates_without_snapshot.append(snapshot_date)
                continue
            report = render_report(
                reader.config, level, period, snapshot_date, False, selection.runnable, inputs
            )
            checked_reports += 1
            snapshot_dates.add(snapshot_date)
            findings += report_findings(report, report_date, level, reader.lead_links)
    return PrivacyAudit(
        checked_reports,
        dates_without_snapshot,
        findings,
        await raw_findings(reader, snapshot_dates) if snapshot_dates else [],
    )


def audit_lines(audit: PrivacyAudit) -> list[str]:
    lines = ["| дата | уровень | модуль | вид нарушения | фрагмент |", "|---|---|---|---|---|"]
    lines += [
        f"| {finding.report_date:%d.%m.%Y} | {finding.level} | {finding.module_id} | "
        f"{finding.violation.kind} | {finding.violation.fragment} |"
        for finding in audit.findings
    ]
    lines += [
        f"База: {finding.table}, {finding.check}: {finding.rows} строк"
        for finding in audit.raw_findings
    ]
    missing = ", ".join(f"{day:%d.%m.%Y}" for day in audit.dates_without_snapshot) or "нет"
    lines.append(
        f"Итог: {len(audit.findings)} нарушений в текстах, "
        f"{len(audit.raw_findings)} в базе; проверено отчётов {audit.checked_reports}; "
        f"дат без снапшота {len(audit.dates_without_snapshot)} ({missing})."
    )
    return lines
