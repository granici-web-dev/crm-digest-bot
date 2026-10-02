from datetime import date, timedelta
from typing import Any

from digest.config import AppConfig
from digest.metrics.daily import PreviousSnapshot
from digest.metrics.frame import prepare_lead_frame
from digest.metrics.source_changes import SourceChange, SourceChanges, source_changes
from digest.metrics.weekly import week_days
from factories import make_snapshot_row

SUNDAY = date(2026, 9, 27)
WEEK = week_days(SUNDAY)
SATURDAY_BEFORE_WEEK = WEEK[0] - timedelta(days=2)
SUNDAY_BEFORE_WEEK = WEEK[0] - timedelta(days=1)


def changes(snapshots: list[tuple[date, list[dict[str, Any]]]], config: AppConfig) -> SourceChanges:
    return source_changes(
        tuple(PreviousSnapshot(day, prepare_lead_frame(rows, config)) for day, rows in snapshots),
        WEEK,
        config,
    )


def lead(lead_id: int, source_name: str | None) -> dict[str, Any]:
    return make_snapshot_row(lead_id=lead_id, source_name=source_name)


def test_changed_source_between_adjacent_snapshots_is_counted(app_config: AppConfig) -> None:
    result = changes(
        [
            (WEEK[0], [lead(1, "Facebook"), lead(2, "Site"), lead(3, "Site")]),
            (WEEK[1], [lead(1, "Showroom"), lead(2, "Telefon"), lead(3, "Site")]),
        ],
        app_config,
    )

    assert result.change_count == 2
    assert result.to_showroom_count == 1
    assert result.pair_count == 1


def test_lead_missing_in_one_snapshot_is_not_a_change(app_config: AppConfig) -> None:
    result = changes(
        [
            (WEEK[0], [lead(1, "Facebook"), lead(2, "Site")]),
            (WEEK[1], [lead(2, "Site"), lead(3, "Showroom")]),
        ],
        app_config,
    )

    assert result == SourceChanges(0, 0, (), 1)


def test_missing_day_gives_one_pair_over_two_days_and_k_six(app_config: AppConfig) -> None:
    result = changes(
        [
            (SUNDAY_BEFORE_WEEK, [lead(1, "Facebook")]),
            *((day, [lead(1, "Facebook")]) for day in WEEK[:3]),
            # WEEK[3] без снапшота: смена четверга видна в паре среда → пятница.
            *((day, [lead(1, "Showroom")]) for day in WEEK[4:]),
        ],
        app_config,
    )

    assert result.change_count == 1
    assert result.to_showroom_count == 1
    assert result.pair_count == 6


def test_null_to_source_is_change_and_null_to_null_is_not(app_config: AppConfig) -> None:
    result = changes(
        [
            (WEEK[0], [lead(1, None), lead(2, None), lead(3, "Site")]),
            (WEEK[1], [lead(1, "Site"), lead(2, None), lead(3, None)]),
        ],
        app_config,
    )

    label = app_config.status_mapping.without_source_label
    assert result.change_count == 2
    assert set(result.top) == {
        SourceChange(label, "Site", 1),
        SourceChange("Site", label, 1),
    }


def test_top_is_three_transitions_by_count_then_name(app_config: AppConfig) -> None:
    before = [
        lead(1, "Facebook"),
        lead(2, "Facebook"),
        lead(3, "Site"),
        lead(4, "Instagram"),
        lead(5, "Telefon"),
        lead(6, "Site"),
    ]
    after = [
        lead(1, "Showroom"),
        lead(2, "Showroom"),
        lead(3, "Showroom"),
        lead(4, "Showroom"),
        lead(5, "Site"),
        lead(6, "Telefon"),
    ]

    result = changes([(WEEK[0], before), (WEEK[1], after)], app_config)

    assert result.change_count == 6
    assert result.top == (
        SourceChange("Facebook", "Showroom", 2),
        SourceChange("Instagram", "Showroom", 1),
        SourceChange("Site", "Showroom", 1),
    )


def test_pair_before_week_is_not_counted(app_config: AppConfig) -> None:
    result = changes(
        [
            (SATURDAY_BEFORE_WEEK, [lead(1, "Facebook")]),
            (SUNDAY_BEFORE_WEEK, [lead(1, "Showroom")]),
            (WEEK[0], [lead(1, "Showroom")]),
        ],
        app_config,
    )

    assert result == SourceChanges(0, 0, (), 1)


def test_no_pairs_gives_zero_changes_and_zero_pairs(app_config: AppConfig) -> None:
    assert changes([(WEEK[0], [lead(1, "Site")])], app_config) == SourceChanges(0, 0, (), 0)
