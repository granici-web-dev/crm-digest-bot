from datetime import date

from syrupy.assertion import SnapshotAssertion

from digest.metrics.daily import SellerFormatCounts, SellerFormatRow
from digest.reports.render import render

REPORT_DATE = date(2026, 8, 12)


def fixed_counts(
    has_previous_snapshot: bool, has_clients_snapshot: bool, without_showroom: int
) -> SellerFormatCounts:
    def row(*values: int) -> SellerFormatRow:
        web, phone, whatsapp, partner, other, visits, offers, contracts = values
        return SellerFormatRow(
            web,
            phone,
            whatsapp,
            partner,
            other,
            visits,
            offers if has_previous_snapshot else None,
            contracts if has_clients_snapshot else None,
        )

    return SellerFormatCounts(
        by_showroom={
            "Brașov": row(1, 1, 0, 0, 0, 1, 1, 0),
            "București": row(0, 1, 2, 0, 0, 3, 0, 0),
            "Cluj": row(0, 0, 0, 0, 0, 2, 2, 0),
        },
        without_showroom_count=without_showroom,
        total=row(2, 2, 2, 0, 0, 6, 3, 0),
        has_previous_snapshot=has_previous_snapshot,
        has_clients_snapshot=has_clients_snapshot,
        unknown_source_lead_ids=(),
        missing_from_previous_lead_ids=(),
    )


def test_seller_format_matches_seller_whatsapp_layout(snapshot: SnapshotAssertion) -> None:
    counts = fixed_counts(has_previous_snapshot=True, has_clients_snapshot=True, without_showroom=1)

    assert (
        render(
            "seller_format_report",
            counts=counts,
            report_date=REPORT_DATE,
            tenant_display_name="Sofabelle",
        )
        == snapshot
    )


def test_seller_format_without_previous_and_clients_snapshots_shows_dashes(
    snapshot: SnapshotAssertion,
) -> None:
    counts = fixed_counts(
        has_previous_snapshot=False, has_clients_snapshot=False, without_showroom=0
    )

    assert (
        render(
            "seller_format_report",
            counts=counts,
            report_date=REPORT_DATE,
            tenant_display_name="Sofabelle",
        )
        == snapshot
    )


def test_seller_format_notes_once_that_showroom_returns_are_not_in_crm() -> None:
    counts = fixed_counts(has_previous_snapshot=True, has_clients_snapshot=True, without_showroom=0)

    text = render(
        "seller_format_report",
        counts=counts,
        report_date=REPORT_DATE,
        tenant_display_name="Sofabelle",
    )

    assert text.count("Revenirile în showroom nu sunt înregistrate în CRM și nu sunt incluse.") == 1
