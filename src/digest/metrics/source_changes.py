from collections import Counter
from dataclasses import dataclass
from datetime import date
from itertools import pairwise

from digest.config import AppConfig
from digest.metrics.daily import PreviousSnapshot

TOP_TRANSITION_COUNT = 3


@dataclass(frozen=True)
class SourceChange:
    from_source: str
    to_source: str
    count: int


@dataclass(frozen=True)
class SourceChanges:
    change_count: int
    to_showroom_count: int
    top: tuple[SourceChange, ...]
    # Пары соседних снапшотов, чей поздний снапшот лежит в периоде: из скольких дней видна неделя.
    pair_count: int


def source_changes(
    snapshots: tuple[PreviousSnapshot, ...], days: tuple[date, ...], config: AppConfig
) -> SourceChanges:
    # docs/kpi-definitions.md, «Смены источника (служебная проверка)»: смена = лид есть в обоих
    # снапшотах соседней пары и source_name различается; пустой с обеих сторон не смена.
    status_mapping = config.status_mapping
    pairs = [
        (previous, current)
        for previous, current in pairwise(snapshots)
        if current.snapshot_date in days
    ]
    transitions: Counter[tuple[str, str]] = Counter()
    to_showroom_count = 0
    for previous, current in pairs:
        merged = current.frame[["lead_id", "source_name"]].merge(
            previous.frame[["lead_id", "source_name"]], on="lead_id", suffixes=("", "_previous")
        )
        new_source, old_source = merged["source_name"], merged["source_name_previous"]
        changed = new_source.ne(old_source) & ~(new_source.isna() & old_source.isna())
        to_showroom_count += int(
            (changed & new_source.isin(status_mapping.sources.showroom_visit)).sum()
        )
        label = status_mapping.without_source_label
        transitions.update(
            zip(
                old_source[changed].fillna(label),
                new_source[changed].fillna(label),
                strict=True,
            )
        )
    ordered = sorted(transitions.items(), key=lambda item: (-item[1], item[0]))
    return SourceChanges(
        sum(transitions.values()),
        to_showroom_count,
        tuple(
            SourceChange(from_source, to_source, count)
            for (from_source, to_source), count in ordered[:TOP_TRANSITION_COUNT]
        ),
        len(pairs),
    )
