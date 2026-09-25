from dataclasses import asdict
from datetime import date
from typing import Any

import pandas as pd

from digest.config import AppConfig, Direction
from digest.metrics.kpi import Period, kpis_from, lead_counts_by_manager


def meets_target(value: float | None, target_value: float, direction: Direction) -> bool | None:
    if value is None:
        return None
    return value >= target_value if direction == "higher" else value <= target_value


def manager_cockpit_table(
    lead_frame: pd.DataFrame, period: Period, analysis_date: date, config: AppConfig
) -> pd.DataFrame:
    # m5: 9 KPI с целями, без SPI и баллов (ADR-002).
    counts_by_manager = lead_counts_by_manager(lead_frame, period, analysis_date, config)
    rows: list[dict[str, Any]] = []
    for manager in config.managers.managers:
        if manager.id not in counts_by_manager:
            continue
        counts = counts_by_manager[manager.id]
        kpis = asdict(kpis_from(counts))
        targets = {
            f"{kpi_name}_meets_target": meets_target(
                kpis[kpi_name], config.kpi.target_value(kpi_name), target.direction
            )
            for kpi_name, target in config.kpi.targets.items()
        }
        rows.append(
            {
                "manager_id": manager.id,
                "name": manager.name,
                "showroom": manager.showroom,
                **asdict(counts),
                **kpis,
                **targets,
            }
        )
    # dtype=object: иначе pandas превратит None в NaN, а «—» в отчёте держится на None.
    return pd.DataFrame(rows, dtype=object)
