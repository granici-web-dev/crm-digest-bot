from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

from digest.config import AppConfig
from digest.metrics.daily import PreviousSnapshot
from digest.metrics.frame import prepare_lead_frame
from digest.metrics.touches import (
    ManagerTouches,
    ManagerTouchGroup,
    followup_levels,
    manager_touches,
    touch_snapshot_dates,
)
from digest.metrics.weekly import week_days
from factories import BUCHAREST, make_snapshot_row

SUNDAY = date(2026, 9, 27)
WEEK = week_days(SUNDAY)
OLD_LEAD_CREATED_AT = datetime(2026, 9, 1, 11, 0, tzinfo=BUCHAREST)
ACTIVE_CONSULTANTS_WITHOUT_TOUCHES = (
    "Godja Adina Maria",
    "Marc Andra",
    "Moaca Andreea",
    "Raileanu  Leon",
    "Roibu Valeria",
)


def lead(lead_id: int, status_name: str, **overrides: Any) -> dict[str, Any]:
    category = "ACTIVE_FOLLOWUP" if status_name.startswith("Revenire") else "ACTIVE"
    values: dict[str, Any] = {
        "created_at": OLD_LEAD_CREATED_AT,
        "last_contact_at": OLD_LEAD_CREATED_AT,
        **overrides,
    }
    return make_snapshot_row(lead_id=lead_id, category=category, status_name=status_name, **values)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=BUCHAREST)


def snapshot(day: date, rows: list[dict[str, Any]], config: AppConfig) -> PreviousSnapshot:
    return PreviousSnapshot(day, prepare_lead_frame(rows, config))


def touches(
    snapshots: list[tuple[date, list[dict[str, Any]]]],
    config: AppConfig,
    days: tuple[date, ...] = WEEK,
) -> ManagerTouches:
    return manager_touches(
        tuple(snapshot(day, rows, config) for day, rows in snapshots), days, config
    )


def touched_group(result: ManagerTouches, manager_name: str | None) -> ManagerTouchGroup:
    [group] = [group for group in result.groups if group.manager_name == manager_name]
    return group


def test_transition_into_revenire_between_snapshots_is_a_touch(app_config: AppConfig) -> None:
    friday, saturday = date(2026, 9, 25), date(2026, 9, 26)
    result = touches(
        [
            (friday, [lead(1, "IN PROCES")]),
            (saturday, [lead(1, "Revenire 1", status_changed_at=at(saturday, 12))]),
        ],
        app_config,
    )

    assert result.touch_count == 1
    assert result.by_level == (1, 0, 0)
    assert touched_group(result, "Dragoi Mihaela") == ManagerTouchGroup(
        "Dragoi Mihaela", 1, (1, 0, 0)
    )


def test_lead_staying_in_same_revenire_is_not_a_touch(app_config: AppConfig) -> None:
    friday, saturday = date(2026, 9, 25), date(2026, 9, 26)
    changed_at = at(saturday, 12)
    result = touches(
        [
            (friday, [lead(1, "Revenire 2", status_changed_at=at(friday, 10))]),
            (saturday, [lead(1, "Revenire 2", status_changed_at=changed_at)]),
        ],
        app_config,
    )

    assert result.touch_count == 0


def test_new_lead_created_in_revenire_is_a_touch_dated_by_created_at(
    app_config: AppConfig,
) -> None:
    thursday, saturday = date(2026, 9, 24), date(2026, 9, 26)
    created_at = at(date(2026, 9, 25), 11)
    new_lead = lead(2, "Revenire 1", created_at=created_at, last_contact_at=created_at)
    result = touches(
        [(thursday, []), (saturday, [new_lead])], app_config, days=(date(2026, 9, 25),)
    )

    assert result.touch_count == 1


def test_lead_missing_from_previous_but_created_earlier_is_not_a_touch(
    app_config: AppConfig,
) -> None:
    friday, saturday = date(2026, 9, 25), date(2026, 9, 26)
    result = touches(
        [(friday, []), (saturday, [lead(3, "Revenire 1", status_changed_at=at(saturday, 12))])],
        app_config,
    )

    assert result.touch_count == 0


def test_level_skip_is_one_touch_at_landing_level(app_config: AppConfig) -> None:
    friday, saturday = date(2026, 9, 25), date(2026, 9, 26)
    changed_at = at(saturday, 12)
    result = touches(
        [
            (friday, [lead(1, "IN PROCES"), lead(2, "Revenire 1")]),
            (
                saturday,
                [
                    lead(1, "Revenire 2", status_changed_at=changed_at),
                    lead(2, "Revenire 3", status_changed_at=changed_at),
                ],
            ),
        ],
        app_config,
    )

    assert result.touch_count == 2
    assert result.by_level == (0, 1, 1)


def test_touch_goes_to_manager_in_later_snapshot(app_config: AppConfig) -> None:
    friday, saturday = date(2026, 9, 25), date(2026, 9, 26)
    reassigned = lead(
        1,
        "Revenire 1",
        status_changed_at=at(saturday, 12),
        assigned_to_id=8,
        assigned_to_name="Roibu Valeria",
    )
    result = touches([(friday, [lead(1, "IN PROCES")]), (saturday, [reassigned])], app_config)

    assert touched_group(result, "Roibu Valeria").touch_count == 1
    assert touched_group(result, "Dragoi Mihaela").touch_count == 0


def test_touch_across_missing_snapshot_is_dated_by_status_changed_at(
    app_config: AppConfig,
) -> None:
    thursday, friday, saturday = date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 26)
    result = touches(
        [
            (thursday, [lead(1, "IN PROCES")]),
            (saturday, [lead(1, "Revenire 1", status_changed_at=at(friday, 12))]),
        ],
        app_config,
        days=(friday,),
    )

    assert result.touch_count == 1
    assert result.days_without_snapshot == (friday,)


def test_touch_dated_before_week_is_not_counted(app_config: AppConfig) -> None:
    previous_sunday, tuesday = date(2026, 9, 20), date(2026, 9, 22)
    result = touches(
        [
            (date(2026, 9, 19), [lead(1, "IN PROCES")]),
            (tuesday, [lead(1, "Revenire 1", status_changed_at=at(previous_sunday, 12))]),
        ],
        app_config,
    )

    assert result.touch_count == 0
    assert result.covered_from == date(2026, 9, 21)
    assert result.days_without_snapshot == (date(2026, 9, 21),)


def test_status_changed_at_outside_pair_is_clamped_to_later_snapshot_day(
    app_config: AppConfig,
) -> None:
    friday, saturday = date(2026, 9, 25), date(2026, 9, 26)
    # Поздний снапшот субботы увидел правку после 19:00, её окно уже воскресное.
    late = lead(1, "Revenire 1", status_changed_at=at(saturday, 20))
    earlier = lead(2, "Revenire 1", status_changed_at=at(date(2026, 9, 23), 12))
    result = touches(
        [(friday, [lead(1, "IN PROCES"), lead(2, "IN PROCES")]), (saturday, [late, earlier])],
        app_config,
        days=(saturday,),
    )

    assert result.touch_count == 2


def test_touch_at_1859_and_1901_fall_into_different_days(app_config: AppConfig) -> None:
    friday, saturday, sunday = date(2026, 9, 25), date(2026, 9, 26), date(2026, 9, 27)
    snapshots = [
        (friday, [lead(1, "IN PROCES"), lead(2, "IN PROCES")]),
        (
            sunday,
            [
                lead(1, "Revenire 1", status_changed_at=at(saturday, 18, 59)),
                lead(2, "Revenire 1", status_changed_at=at(saturday, 19, 1)),
            ],
        ),
    ]

    assert touches(snapshots, app_config, days=(saturday,)).touch_count == 1
    assert touches(snapshots, app_config, days=(sunday,)).touch_count == 1


def test_touch_days_across_winter_time_change(app_config: AppConfig) -> None:
    # 25.10.2026 часы переводятся назад: окно 25.10 длится 25 часов.
    saturday, dst_sunday, monday = date(2026, 10, 24), date(2026, 10, 25), date(2026, 10, 26)
    before = [lead(1, "IN PROCES"), lead(2, "IN PROCES")]
    after = [
        lead(1, "Revenire 1", status_changed_at=at(dst_sunday, 18, 59)),
        lead(2, "Revenire 1", status_changed_at=at(dst_sunday, 19, 1)),
    ]
    snapshots = [(saturday, before), (monday, after)]

    assert touches(snapshots, app_config, days=(dst_sunday,)).touch_count == 1
    assert touches(snapshots, app_config, days=(monday,)).touch_count == 1


def test_levels_follow_status_mapping_order(app_config: AppConfig) -> None:
    categories = app_config.status_mapping.categories
    followup = categories.ACTIVE_FOLLOWUP.model_copy(
        update={"statuses": ["Sunat 2", "Sunat 1", "Sunat 3", "Sunat 4"]}
    )
    status_mapping = app_config.status_mapping.model_copy(
        update={"categories": categories.model_copy(update={"ACTIVE_FOLLOWUP": followup})}
    )
    config = app_config.model_copy(update={"status_mapping": status_mapping})

    levels = followup_levels(pd.Series(["Sunat 1", "Sunat 4", "Revenire 1", None]), config)

    assert levels.tolist() == [2, 4, pd.NA, pd.NA]


def test_active_consultant_without_touches_is_listed_with_zero(app_config: AppConfig) -> None:
    friday, saturday = date(2026, 9, 25), date(2026, 9, 26)
    result = touches(
        [
            (friday, [lead(1, "IN PROCES")]),
            (saturday, [lead(1, "Revenire 1", status_changed_at=at(saturday, 12))]),
        ],
        app_config,
    )

    assert [group.manager_name for group in result.groups] == [
        "Dragoi Mihaela",
        *ACTIVE_CONSULTANTS_WITHOUT_TOUCHES,
    ]
    assert touched_group(result, "Marc Andra") == ManagerTouchGroup("Marc Andra", 0, (0, 0, 0))


def test_not_taken_group_is_first(app_config: AppConfig) -> None:
    friday, saturday = date(2026, 9, 25), date(2026, 9, 26)
    changed_at = at(saturday, 12)
    marketing = {"assigned_to_id": 7, "assigned_to_name": "Marketing Sofa"}
    result = touches(
        [
            (friday, [lead(lead_id, "IN PROCES") for lead_id in (1, 2, 3)]),
            (
                saturday,
                [
                    lead(1, "Revenire 1", status_changed_at=changed_at),
                    lead(2, "Revenire 1", status_changed_at=changed_at),
                    lead(3, "Revenire 2", status_changed_at=changed_at, **marketing),
                ],
            ),
        ],
        app_config,
    )

    assert result.groups[:2] == (
        ManagerTouchGroup(None, 1, (0, 1, 0)),
        ManagerTouchGroup("Dragoi Mihaela", 2, (2, 0, 0)),
    )


def test_of_manager_keeps_one_group_and_coverage(app_config: AppConfig) -> None:
    friday, saturday = date(2026, 9, 25), date(2026, 9, 26)
    result = touches(
        [
            (friday, [lead(1, "IN PROCES")]),
            (saturday, [lead(1, "Revenire 3", status_changed_at=at(saturday, 12))]),
        ],
        app_config,
    )

    assert result.of_manager("Dragoi Mihaela") == ManagerTouches(
        1,
        (0, 0, 1),
        (ManagerTouchGroup("Dragoi Mihaela", 1, (0, 0, 1)),),
        result.days_without_snapshot,
        result.covered_from,
    )
    assert result.of_manager("Nimeni").touch_count == 0


def test_full_week_chain_covers_every_day(app_config: AppConfig) -> None:
    daily = [
        (SUNDAY - timedelta(days=offset), [lead(1, "IN PROCES")]) for offset in range(7, -1, -1)
    ]

    result = touches(daily, app_config)

    assert result.covered_from == WEEK[0]
    assert result.days_without_snapshot == ()


def test_single_snapshot_covers_nothing(app_config: AppConfig) -> None:
    result = touches([(SUNDAY, [lead(1, "Revenire 1")])], app_config)

    assert result.covered_from is None
    assert result.touch_count == 0


def test_touch_snapshot_dates_starts_from_last_success_before_week() -> None:
    success = (date(2026, 9, 18), date(2026, 9, 19), date(2026, 9, 22), date(2026, 9, 27))

    assert touch_snapshot_dates(success, WEEK[0], SUNDAY) == (
        date(2026, 9, 19),
        date(2026, 9, 22),
        date(2026, 9, 27),
    )


def test_touch_snapshot_dates_without_earlier_snapshot_sets_covered_from(
    app_config: AppConfig,
) -> None:
    success = (date(2026, 9, 24), date(2026, 9, 27), date(2026, 9, 28))
    chain = touch_snapshot_dates(success, WEEK[0], SUNDAY)
    result = touches([(day, [lead(1, "IN PROCES")]) for day in chain], app_config)

    assert chain == (date(2026, 9, 24), date(2026, 9, 27))
    assert result.covered_from == date(2026, 9, 25)
    assert result.days_without_snapshot == (date(2026, 9, 25), date(2026, 9, 26))
