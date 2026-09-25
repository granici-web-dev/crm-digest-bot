from dataclasses import asdict
from datetime import date
from typing import Any

import pandas as pd

from digest.config import KPI_NAMES, AppConfig, Direction
from digest.metrics.kpi import COUNT_NAMES, Period, kpis_from, lead_counts_by_manager


def meets_target(value: float | None, target_value: float, direction: Direction) -> bool | None:
    if value is None:
        return None
    return value >= target_value if direction == "higher" else value <= target_value


def manager_cockpit_table(
    lead_frame: pd.DataFrame, period: Period, analysis_date: date, config: AppConfig
) -> pd.DataFrame:
    # m5: 9 KPI с целями, без SPI и баллов (ADR-002). Цель выполнена при >= или <= включительно,
    # KPI = null → цель неизвестна (docs/kpi-definitions.md, «KPI»).
    counts_by_manager = lead_counts_by_manager(lead_frame, period, analysis_date, config)
    managers_by_id = {manager.id: manager for manager in config.managers.managers}
    rows: list[dict[str, Any]] = []
    for manager_id, counts in counts_by_manager.items():
        manager = managers_by_id[manager_id]
        kpis = asdict(kpis_from(counts))
        targets = {
            f"{kpi_name}_meets_target": meets_target(
                kpis[kpi_name], config.kpi.target_value(kpi_name), target.direction
            )
            for kpi_name, target in config.kpi.targets.items()
        }
        rows.append(
            {
                "manager_id": manager_id,
                "name": manager.name,
                "showroom": manager.showroom,
                **asdict(counts),
                **kpis,
                **targets,
            }
        )
    table = pd.DataFrame(rows, columns=["manager_id", "name", "showroom", *COUNT_NAMES])
    # object только для KPI и целей: иначе pandas превратит None в NaN, а «—» в отчёте держится
    # на None; счётчики остаются int.
    for column in [*KPI_NAMES, *(f"{kpi_name}_meets_target" for kpi_name in config.kpi.targets)]:
        table[column] = pd.Series([row[column] for row in rows], dtype=object)
    return table
