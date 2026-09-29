import re
from io import BytesIO
from pathlib import Path

import pytest
import xlsxwriter
from test_weekly_excel import SUNDAY, week_frame

from digest.acceptance.privacy import privacy_violations, xlsx_violations
from digest.config import AppConfig
from digest.reports.context import ReportContext
from digest.reports.lead_links import LeadLinks
from digest.reports.modules.weekly_excel import excel_attachment_report
from factories import make_lead_links

SNAPSHOTS_DIR = Path(__file__).parent / "__snapshots__"
SNAPSHOT_ENTRY = re.compile(r"^# name: (.+?)\n(.*?)^# ---", re.MULTILINE | re.DOTALL)


@pytest.fixture
def lead_links(app_config: AppConfig) -> LeadLinks:
    return make_lead_links(app_config.status_mapping)


def link(lead_links: LeadLinks, lead_id: int) -> str:
    return str(lead_links.link(lead_id))


def rendered_snapshots() -> list[tuple[str, str]]:
    return [
        (f"{path.name}::{name}", body)
        for path in sorted(SNAPSHOTS_DIR.glob("*.ambr"))
        for name, body in SNAPSHOT_ENTRY.findall(path.read_text(encoding="utf-8"))
    ]


def test_every_rendered_report_snapshot_is_clean(lead_links: LeadLinks) -> None:
    snapshots = rendered_snapshots()

    violations = {
        name: privacy_violations(body, lead_links)
        for name, body in snapshots
        if privacy_violations(body, lead_links)
    }

    assert len(snapshots) >= 40
    assert violations == {}


@pytest.mark.parametrize(
    "value",
    [
        "Sunați la 0712 345 678",
        "tel +40 (712) 345-678",
        "0712\u200b345\u200b678",
        "0712\u00a0345\u00a0678",
        "0712 34-56-78",
        "0712.34.56.78",
    ],
)
def test_phone_is_found(value: str, lead_links: LeadLinks) -> None:
    [violation] = privacy_violations(value, lead_links)

    assert violation.kind == "phone"
    assert not re.search(r"\d", violation.fragment)


def test_email_is_found_without_local_part(lead_links: LeadLinks) -> None:
    [violation] = privacy_violations("scrieți la client1@example.com", lead_links)

    assert violation.kind == "email"
    assert violation.fragment == "•••@example.com"


def test_eleven_links_in_one_block_are_a_violation(lead_links: LeadLinks) -> None:
    links = ", ".join(link(lead_links, lead_id) for lead_id in range(1, 12))

    [violation] = privacy_violations(f"<b>Restanțe</b>\n{links}", lead_links)

    assert violation.kind == "too_many_links"


def test_foreign_link_is_a_violation(lead_links: LeadLinks) -> None:
    [violation] = privacy_violations('<a href="https://example.com/c/42">#42</a>', lead_links)

    assert violation.kind == "foreign_link"


@pytest.mark.parametrize(
    "value",
    [
        "24.09.2026 19:00 → 25.09.2026 19:00",
        "Snapshot 28.09, ora 19:05",
        "SCR 9,6% (+17,4%)",
        "Lead #12345 fără Data revenire",
        "<code>București   12   34   56   78</code>",
    ],
)
def test_report_numbers_are_not_contacts(value: str, lead_links: LeadLinks) -> None:
    assert privacy_violations(value, lead_links) == []


def test_ten_links_per_block_and_remainder_are_allowed(lead_links: LeadLinks) -> None:
    first = ", ".join(link(lead_links, lead_id) for lead_id in range(1, 11))
    second = ", ".join(link(lead_links, lead_id) for lead_id in range(11, 21))

    value = f"<b>Restanțe</b>\n{first} și încă 3\n<b>Fără Data revenire</b>\n{second}"

    assert privacy_violations(value, lead_links) == []


def test_weekly_workbook_is_clean(app_config: AppConfig) -> None:
    context = ReportContext(
        SUNDAY,
        None,
        None,
        None,
        (),
        None,
        app_config,
        "sofabelle",
        make_lead_links(app_config.status_mapping),
    )
    document = excel_attachment_report(week_frame(app_config), context).document
    assert document is not None

    assert xlsx_violations(document.content) == []


def test_phone_in_workbook_cell_is_found() -> None:
    buffer = BytesIO()
    book = xlsxwriter.Workbook(buffer)
    sheet = book.add_worksheet()
    sheet.write_string(0, 0, "Lead")
    sheet.write_string(1, 0, "+40700000001")
    sheet.write_number(1, 1, 0.123456789)
    sheet.write_number(1, 2, 1234)
    book.close()

    [violation] = xlsx_violations(buffer.getvalue())

    assert violation.kind == "phone"
