"""Build tests/fixtures/etalon-2026-05.json from docs/reference/SB KPi.xlsx.

Usage: uv run python scripts/build_etalon.py

Prints only counts; lead data never goes to stdout (the workbook holds client contacts).
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "docs" / "reference" / "SB KPi.xlsx"
TARGET = ROOT / "tests" / "fixtures" / "etalon-2026-05.json"
TZ = ZoneInfo("Europe/Bucharest")

LEADS_SHEET = "01_Input_Leads"
SETTINGS_SHEET = "02_Setari_Targete"
KPI_SHEET = "03_KPI_Agenti"
KPI_HEADER_ROW = 3
KPI_ROWS = range(4, 10)
KPI_FIRST_COL, KPI_LAST_COL = 3, 31  # C..AE

# Excel header -> etalon field. Nume, Telefon, E-mail, Informatii, Revenire 1-3, Oraș,
# Interacțiuni and helper columns V..AG are deliberately absent.
LEAD_FIELDS = {
    "Data ultimei solicitări": "created_at",
    "Ultimul contact": "last_contact_at",
    "Status": "status",
    "Ofertat": "ofertat",
    "Desemnat": "assigned_to",
    "Showroom": "showroom",
    "Sursa": "source",
    "Data revenire": "data_revenire",
    "UTM_Source": "utm_source",
    "UTM_Campanie": "utm_campaign",
    "UTM_Content": "utm_content",
    "UTM_Medium": "utm_medium",
}
DATETIME_FIELDS = {"created_at", "last_contact_at"}
DATE_FIELDS = {"data_revenire"}
PII_HEADERS = ("Nume", "Telefon", "E-mail")
# Single-word client names coincide with consultant first names and source labels, so
# substring search is limited to identifying shapes: full names, e-mails, phone digits.
PII_SUBSTRING_MIN_LEN = 6
PHONE_MIN_DIGITS = 7


def snake(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", ascii_text.lower()).strip("_")


def parse_datetime(value: Any) -> str | None:
    if value in (None, ""):
        return None
    local = datetime.strptime(str(value), "%d-%m-%Y %H:%M:%S").replace(tzinfo=TZ)
    return local.isoformat()


def parse_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return datetime.strptime(str(value)[:10], "%d-%m-%Y").date().isoformat()


def read_leads(ws: Any) -> tuple[list[dict[str, Any]], set[str]]:
    header = [cell.value for cell in ws[1]]
    col = {name: i for i, name in enumerate(header) if name is not None}
    missing = [h for h in (*LEAD_FIELDS, *PII_HEADERS) if h not in col]
    if missing:
        raise SystemExit(f"{LEADS_SHEET}: missing columns {missing}")

    leads: list[dict[str, Any]] = []
    pii: set[str] = set()
    for row_no, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        for h in PII_HEADERS:
            if row[col[h]] not in (None, ""):
                pii.add(str(row[col[h]]).strip())
        if row[col["Status"]] in (None, ""):
            continue
        lead: dict[str, Any] = {"row": row_no}
        for header_name, field in LEAD_FIELDS.items():
            value = row[col[header_name]]
            if field in DATETIME_FIELDS:
                value = parse_datetime(value)
            elif field in DATE_FIELDS:
                value = parse_date(value)
            elif value == "":
                value = None
            lead[field] = value
        leads.append(lead)
    return leads, pii


def read_thresholds(ws: Any) -> tuple[str, dict[str, float]]:
    analysis_date = ws["B3"].value
    if not isinstance(analysis_date, datetime):
        raise SystemExit(f"{SETTINGS_SHEET}!B3 is not a date")
    thresholds: dict[str, float] = {}
    for row in range(4, ws.max_row + 1):
        label, value = ws.cell(row, 1).value, ws.cell(row, 2).value
        if label is None or value is None:
            continue
        thresholds[snake(str(label))] = value
    return analysis_date.date().isoformat(), thresholds


def read_expected(ws: Any) -> dict[str, dict[str, Any]]:
    headers = {
        c: snake(str(ws.cell(KPI_HEADER_ROW, c).value))
        for c in range(KPI_FIRST_COL, KPI_LAST_COL + 1)
    }
    expected: dict[str, dict[str, Any]] = {}
    for row in KPI_ROWS:
        agent = ws.cell(row, 1).value
        values: dict[str, Any] = {"showroom": ws.cell(row, 2).value}
        values.update({key: ws.cell(row, c).value for c, key in headers.items()})
        expected[str(agent)] = values
    return expected


def leaked_pii(payload: Any, pii: set[str], dump: str) -> list[str]:
    leaks: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif node is not None and str(node).strip() in pii:
            leaks.append("exact")

    walk(payload)
    dump_digits = re.sub(r"\D", "", dump)
    for p in pii:
        digits = re.sub(r"\D", "", p)
        if len(digits) >= PHONE_MIN_DIGITS and digits in dump_digits:
            leaks.append("phone")
        elif len(p) >= PII_SUBSTRING_MIN_LEN and (" " in p or "@" in p) and p in dump:
            leaks.append("substring")
    return leaks


def main() -> None:
    # openpyxl warns about unsupported Excel extensions (conditional formatting); irrelevant here.
    warnings.filterwarnings("ignore", module="openpyxl")
    wb = openpyxl.load_workbook(SOURCE, data_only=True, read_only=False)

    leads, pii = read_leads(wb[LEADS_SHEET])
    analysis_date, thresholds = read_thresholds(wb[SETTINGS_SHEET])
    expected = read_expected(wb[KPI_SHEET])
    created = sorted(lead["created_at"] for lead in leads if lead["created_at"])

    payload = {
        "meta": {
            "source_file": SOURCE.name,
            "analysis_date": analysis_date,
            "timezone": TZ.key,
            "date_range": {"from": created[0], "to": created[-1]},
            "lead_count": len(leads),
        },
        "thresholds": thresholds,
        "leads": leads,
        "expected_by_agent": expected,
    }
    dump = json.dumps(payload, ensure_ascii=False, indent=2)

    leaks = leaked_pii(payload, pii, dump)
    if leaks:
        # Count only: printing the matched value would itself leak it.
        sys.exit(f"PII check failed: {len(leaks)} match(es), etalon not written")

    TARGET.write_text(dump + "\n", encoding="utf-8")
    print(
        f"leads={len(leads)} thresholds={len(thresholds)} agents={len(expected)} "
        f"pii_values_checked={len(pii)} -> {TARGET.relative_to(ROOT)}"
    )


if __name__ == "__main__":
    main()
