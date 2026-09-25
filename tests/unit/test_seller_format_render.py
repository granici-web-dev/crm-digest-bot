from datetime import date

import pytest
from syrupy.assertion import SnapshotAssertion

from digest.metrics.daily import SellerFormatCounts, SellerFormatRow
from digest.reports.render import ReportLanguage, render

REPORT_DATE = date(2026, 8, 12)


def fixed_counts(has_previous_snapshot: bool, without_showroom: int) -> SellerFormatCounts:
    def row(*values: int) -> SellerFormatRow:
        leads, transitions = values[:5], values[5:]
        return SellerFormatRow(
            *leads, *(transitions if has_previous_snapshot else (None, None, None))
        )

    return SellerFormatCounts(
        by_showroom={
            "Brașov": row(1, 1, 0, 0, 0, 1, 1, 0),
            "București": row(0, 1, 2, 0, 0, 3, 0, 0),
            "Cluj": row(0, 0, 0, 0, 0, 2, 2, 0),
        },
        without_showroom_lead_count=without_showroom,
        total=row(2, 2, 2, 0, 0, 6, 3, 0),
        has_previous_snapshot=has_previous_snapshot,
        unknown_source_lead_ids=(),
        missing_from_previous_lead_ids=(),
    )


@pytest.mark.parametrize("language", ["ro", "ru"])
def test_seller_format_matches_seller_whatsapp_layout(
    language: ReportLanguage, snapshot: SnapshotAssertion
) -> None:
    counts = fixed_counts(has_previous_snapshot=True, without_showroom=1)

    assert (
        render("seller_format_report", language, counts=counts, report_date=REPORT_DATE) == snapshot
    )


@pytest.mark.parametrize("language", ["ro", "ru"])
def test_seller_format_without_previous_snapshot_shows_dashes(
    language: ReportLanguage, snapshot: SnapshotAssertion
) -> None:
    counts = fixed_counts(has_previous_snapshot=False, without_showroom=0)

    assert (
        render("seller_format_report", language, counts=counts, report_date=REPORT_DATE) == snapshot
    )
