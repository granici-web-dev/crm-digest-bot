from dataclasses import dataclass
from datetime import date
from itertools import pairwise

import pandas as pd

from digest.config import AppConfig
from digest.metrics.daily import PreviousSnapshot, daily_window, daily_window_days
from digest.metrics.daily_checks import manager_names, name_or_not_taken, not_taken_first

TOUCH_ROW_COLUMNS = ("lead_id", "level", "assigned_to_id", "assigned_to_name", "touch_day")


@dataclass(frozen=True)
class ManagerTouchGroup:
    # None: лид не взят (консультант с not_taken или без консультанта).
    manager_name: str | None
    touch_count: int
    # Касания по уровням Revenire: позиция i = статус i в ACTIVE_FOLLOWUP.statuses.
    by_level: tuple[int, ...]


@dataclass(frozen=True)
class ManagerTouches:
    touch_count: int
    by_level: tuple[int, ...]
    groups: tuple[ManagerTouchGroup, ...]
    # Покрытые дни без своего снапшота: касание дня, после которого лид ушёл дальше до
    # следующего снапшота, потеряно.
    days_without_snapshot: tuple[date, ...]
    # Первый день периода, который видит цепочка; None: не покрыт ни один день.
    covered_from: date | None

    def of_manager(self, manager_name: str) -> "ManagerTouches":
        # Консультант без касаний и вне active это ноль по построению, а не отсутствие группы.
        groups = tuple(group for group in self.groups if group.manager_name == manager_name)
        by_level = tuple(
            sum(group.by_level[level] for group in groups) for level in range(len(self.by_level))
        )
        return ManagerTouches(
            sum(by_level), by_level, groups, self.days_without_snapshot, self.covered_from
        )


def followup_levels(status_name: pd.Series, config: AppConfig) -> pd.Series:
    # Уровень касания = позиция статуса в ACTIVE_FOLLOWUP.statuses (docs/kpi-definitions.md,
    # «Касания консультантов»): новый Revenire 4 в конфиге станет четвёртым уровнем без правки кода.
    statuses = config.status_mapping.categories.ACTIVE_FOLLOWUP.statuses
    levels = {status: position for position, status in enumerate(statuses, start=1)}
    return status_name.map(levels).astype("Int64")


def touch_snapshot_dates(
    success_dates: tuple[date, ...], first_day: date, last_day: date
) -> tuple[date, ...]:
    # Цепочка начинается с последнего снапшота до периода: его пара с первым снапшотом периода
    # видит касания первого дня.
    before = [day for day in success_dates if day < first_day]
    inside = [day for day in success_dates if first_day <= day <= last_day]
    return tuple(before[-1:] + inside)


def touch_rows(
    previous: PreviousSnapshot, current: PreviousSnapshot, config: AppConfig
) -> pd.DataFrame:
    # docs/kpi-definitions.md, «Касания консультантов»: касание = лид в Revenire N в current,
    # а в previous в другом статусе или новый. Разница снапшотов решает, было ли касание,
    # status_changed_at его только датирует (инвариант 3).
    frame = current.frame
    level = followup_levels(frame["status_name"], config)
    merged = frame[["lead_id", "status_name"]].merge(
        previous.frame[["lead_id", "status_name"]],
        on="lead_id",
        how="left",
        suffixes=("", "_previous"),
        indicator=True,
    )
    in_previous = merged["_merge"].eq("both").to_numpy()
    status_changed = merged["status_name"].ne(merged["status_name_previous"]).to_numpy()
    time_settings = config.status_mapping.time
    created_after_previous = frame["created_at"].ge(
        daily_window(previous.snapshot_date, time_settings).end
    )
    touched = level.notna() & (
        (in_previous & status_changed) | (~in_previous & created_after_previous)
    )
    touches = frame[touched]
    # status_changed_at = null, если статус задан при создании (CLAUDE.md): новый лид сразу в
    # Revenire датируется created_at. День вне пары (правка до previous, поздний снапшот)
    # прижимается к дню current: касание не теряется.
    touch_day = daily_window_days(
        touches["status_changed_at"].fillna(touches["created_at"]), time_settings
    )
    inside_pair = touch_day.gt(previous.snapshot_date) & touch_day.le(current.snapshot_date)
    return pd.DataFrame(
        {
            "lead_id": touches["lead_id"],
            "level": level[touched],
            "assigned_to_id": touches["assigned_to_id"],
            "assigned_to_name": touches["assigned_to_name"],
            "touch_day": touch_day.where(inside_pair, current.snapshot_date),
        },
        columns=list(TOUCH_ROW_COLUMNS),
    )


def manager_touches(
    snapshots: tuple[PreviousSnapshot, ...], days: tuple[date, ...], config: AppConfig
) -> ManagerTouches:
    # docs/kpi-definitions.md, «Касания консультантов»: период = цепочка пар соседних успешных
    # снапшотов, касания фильтруются по дню касания.
    level_count = len(config.status_mapping.categories.ACTIVE_FOLLOWUP.statuses)
    pairs = list(pairwise(snapshots))
    pair_rows = [touch_rows(previous, current, config) for previous, current in pairs]
    rows = (
        pd.concat(pair_rows, ignore_index=True)
        if pair_rows
        else pd.DataFrame(columns=list(TOUCH_ROW_COLUMNS))
    )
    rows = rows[rows["touch_day"].isin(days)]

    covered_days = [
        day
        for previous, current in pairs
        for day in days
        if previous.snapshot_date < day <= current.snapshot_date
    ]
    snapshot_dates = {snapshot.snapshot_date for snapshot in snapshots}
    days_without_snapshot = tuple(sorted(day for day in covered_days if day not in snapshot_dates))

    manager_name = manager_names(rows, config)
    groups: dict[object, ManagerTouchGroup] = {}
    for manager_id, group_rows in rows.groupby(
        rows["assigned_to_id"].where(manager_name.notna()), dropna=False, sort=False
    ):
        levels = group_rows["level"].astype(int)
        groups[manager_id] = ManagerTouchGroup(
            name_or_not_taken(manager_name[group_rows.index].iloc[0]),
            len(group_rows),
            tuple(int(levels.eq(level).sum()) for level in range(1, level_count + 1)),
        )
    # Ноль у активного консультанта это сигнал, строка остаётся.
    for manager in config.managers.managers:
        if manager.active and manager.id not in groups:
            groups[manager.id] = ManagerTouchGroup(manager.name, 0, (0,) * level_count)
    ordered = sorted(
        groups.values(), key=lambda group: not_taken_first(group.manager_name, group.touch_count)
    )
    by_level = tuple(
        sum(group.by_level[level] for group in ordered) for level in range(level_count)
    )
    return ManagerTouches(
        len(rows),
        by_level,
        tuple(ordered),
        days_without_snapshot,
        min(covered_days) if covered_days else None,
    )
