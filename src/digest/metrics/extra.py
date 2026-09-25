from datetime import date

import pandas as pd

from digest.config import AppConfig
from digest.metrics.kpi import Period, kpis_from, lead_counts

OPEN_CATEGORIES = ("ACTIVE", "ACTIVE_FOLLOWUP", "UNMAPPED")


def overdue_revenire(lead_frame: pd.DataFrame, today: date, config: AppConfig) -> pd.DataFrame:
    # docs/kpi-definitions.md, «Дополнительные метрики»: из LOST только причины с полем
    # followup_field в status-mapping.yaml (Stand BY, бриф §3).
    followup_reasons = [
        reason_name
        for reason_name, reason in config.status_mapping.categories.LOST.reasons.items()
        if reason.followup_field is not None
    ]
    eligible = lead_frame["category"].isin(OPEN_CATEGORIES) | (
        lead_frame["category"].eq("LOST") & lead_frame["loss_reason"].isin(followup_reasons)
    )
    revenire_day = lead_frame["data_revenire"]
    status_change_day = lead_frame["status_changed_at"].dt.tz_localize(None).dt.normalize()
    # status_changed_at = null: статус задан при создании и с тех пор не менялся (CLAUDE.md).
    untouched_since_revenire = status_change_day.isna() | status_change_day.lt(revenire_day)
    due = revenire_day.le(pd.Timestamp(today))
    overdue: pd.DataFrame = lead_frame.loc[eligible & due & untouched_since_revenire]
    return overdue


def cohort_conversion(lead_frame: pd.DataFrame, cohort: Period, config: AppConfig) -> float | None:
    # docs/kpi-definitions.md, «Дополнительные метрики»: когорта = SCR лидов, созданных в периоде
    # cohort, по снапшоту, из которого взят lead_frame. analysis_date влияет только на
    # ACTIVE_OFFERS_14, в SCR не входит.
    counts = lead_counts(lead_frame, cohort, cohort.end.date(), config)
    return kpis_from(counts).scr


def period_delta(current: float | None, previous: float | None) -> float | None:
    # docs/kpi-definitions.md, «Дополнительные метрики»: относительное изменение,
    # от нуля или от null не считается.
    if current is None or previous is None or previous == 0:
        return None
    return (current - previous) / previous
