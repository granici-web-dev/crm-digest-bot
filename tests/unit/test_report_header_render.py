from datetime import date, datetime, time

from syrupy.assertion import SnapshotAssertion

from digest.reports.render import render
from factories import BUCHAREST


def render_daily_header(late: bool, late_snapshot_at: datetime | None) -> str:
    return render(
        "report",
        blocks=[],
        snapshot_missing=False,
        unavailable_sources=[],
        late=late,
        late_snapshot_at=late_snapshot_at,
        daily_window_end=time(19, 0),
        level="daily",
        period_label="27.09.2026 19:00 → 28.09.2026 19:00",
        snapshot_date=date(2026, 9, 28),
        tenant_display_name="Sofabelle",
    )


def test_report_sent_late_with_late_snapshot(snapshot: SnapshotAssertion) -> None:
    text = render_daily_header(True, datetime(2026, 9, 28, 21, 47, tzinfo=BUCHAREST))

    assert text.splitlines()[0] == "Raport trimis cu întârziere"
    assert text == snapshot


def test_report_on_time_has_no_late_lines(snapshot: SnapshotAssertion) -> None:
    text = render_daily_header(False, None)

    assert "întârziere" not in text
    assert "Date extrase" not in text
    assert text == snapshot
