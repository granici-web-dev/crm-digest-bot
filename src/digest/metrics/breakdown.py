from dataclasses import dataclass
from datetime import date
from typing import Literal, get_args

import pandas as pd

from digest.config import AppConfig
from digest.metrics.kpi import (
    COUNT_NAMES,
    Kpis,
    LeadCounts,
    Period,
    add_counts,
    count_flags,
    counts_from_sums,
    kpis_from,
    ratio,
)

BreakdownColumn = Literal["source_name", "utm_campanie"]
BREAKDOWN_COLUMNS: tuple[BreakdownColumn, ...] = get_args(BreakdownColumn)


@dataclass(frozen=True)
class BreakdownRow:
    # Значение как в mefi.
    key: str
    counts: LeadCounts
    kpis: Kpis


@dataclass(frozen=True)
class UnkeyedBreakdownRow:
    # Свёрнутые ключи, лиды без значения или итог.
    counts: LeadCounts
    kpis: Kpis


@dataclass(frozen=True)
class LeadBreakdown:
    # Ключи с leads >= min_leads, по убыванию лидов, при равенстве по имени.
    rows: tuple[BreakdownRow, ...]
    other: UnkeyedBreakdownRow | None
    other_keys: tuple[str, ...]
    # Лиды без значения не сворачиваются в other: это вопрос заполнения поля в mefi.
    without_key: UnkeyedBreakdownRow | None
    total: UnkeyedBreakdownRow

    # docs/kpi-definitions.md, «Разбивка по источникам и кампаниям» (m7): доля «N din M lead-uri
    # au campanie» = LEADS с ключом / все LEADS окна, лиды без значения только в знаменателе.
    # Одна формула для m7 и чата.
    @property
    def leads_with_key(self) -> int:
        without_key_leads = 0 if self.without_key is None else self.without_key.counts.leads
        return self.total.counts.leads - without_key_leads

    @property
    def key_share(self) -> float | None:
        return ratio(self.leads_with_key, self.total.counts.leads)


def unkeyed_row(counts: LeadCounts) -> UnkeyedBreakdownRow:
    return UnkeyedBreakdownRow(counts, kpis_from(counts))


def lead_counts_by_column(
    lead_frame: pd.DataFrame,
    column: BreakdownColumn,
    period: Period,
    analysis_date: date,
    config: AppConfig,
) -> dict[str | None, LeadCounts]:
    # Те же флаги, что у lead_counts (docs/kpi-definitions.md, «Разбивка по источникам и
    # кампаниям»): сумма по ключам равна счётчикам компании на том же окне по построению.
    flags = count_flags(lead_frame, period, analysis_date, config)
    keys = lead_frame.loc[flags.index, column]
    sums = flags[list(COUNT_NAMES)].groupby(keys, dropna=False).sum()
    return {
        None if pd.isna(key) else str(key): counts_from_sums(row)
        for key, row in zip(sums.index.tolist(), sums.to_dict("records"), strict=True)
    }


def lead_breakdown(
    lead_frame: pd.DataFrame,
    column: BreakdownColumn,
    period: Period,
    analysis_date: date,
    config: AppConfig,
    min_leads: int,
) -> LeadBreakdown:
    counts_by_key = lead_counts_by_column(lead_frame, column, period, analysis_date, config)
    named = {key: counts for key, counts in counts_by_key.items() if key is not None}
    kept = sorted(
        (key for key, counts in named.items() if counts.leads >= min_leads),
        key=lambda key: (-named[key].leads, key),
    )
    collapsed = tuple(sorted(key for key, counts in named.items() if counts.leads < min_leads))
    without_key = counts_by_key.get(None)
    return LeadBreakdown(
        rows=tuple(BreakdownRow(key, named[key], kpis_from(named[key])) for key in kept),
        other=unkeyed_row(add_counts(named[key] for key in collapsed)) if collapsed else None,
        other_keys=collapsed,
        without_key=None if without_key is None else unkeyed_row(without_key),
        total=unkeyed_row(add_counts(counts_by_key.values())),
    )
