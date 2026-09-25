from typing import Any

import pandas as pd

from digest.config import AppConfig

LEAD_FRAME_COLUMNS = (
    "lead_id",
    "category",
    "loss_reason",
    "status_name",
    "source_name",
    "showroom",
    "ofertat",
    "data_revenire",
    "is_duplicate",
    "assigned_to_id",
    "assigned_to_name",
    "created_at",
    "status_changed_at",
    "last_contact_at",
    "converted_at",
)
TIMESTAMP_COLUMNS = ("created_at", "status_changed_at", "last_contact_at", "converted_at")


def prepare_lead_frame(rows: list[dict[str, Any]], config: AppConfig) -> pd.DataFrame:
    lead_frame = pd.DataFrame.from_records(rows, columns=list(LEAD_FRAME_COLUMNS))
    lead_frame["assigned_to_id"] = lead_frame["assigned_to_id"].astype("Int64")
    test_account_ids = [manager.id for manager in config.managers.managers if manager.test_account]
    # Лиды тестовых аккаунтов вне всех метрик (docs/kpi-definitions.md, «Базовые множества»).
    lead_frame = lead_frame[~lead_frame["assigned_to_id"].isin(test_account_ids).fillna(False)]
    lead_frame = lead_frame.reset_index(drop=True)

    timezone = config.status_mapping.time.timezone
    for column in TIMESTAMP_COLUMNS:
        lead_frame[column] = pd.to_datetime(lead_frame[column], utc=True).dt.tz_convert(timezone)

    # Флаги только из category и loss_reason: статусы mefi живут в status-mapping.yaml.
    category, loss_reason = lead_frame["category"], lead_frame["loss_reason"]
    lead_frame["is_clienti"] = category.eq("WON")
    lead_frame["is_partnership"] = category.eq("PARTNERSHIP")
    lead_frame["is_irelevant"] = loss_reason.eq("IRELEVANT")
    lead_frame["is_nu_a_raspuns"] = loss_reason.eq("NU_RASPUNS")
    lead_frame["is_buget"] = loss_reason.eq("BUGET")
    lead_frame["is_produs_nepotrivit"] = loss_reason.eq("PRODUS_NEPOTRIVIT")
    # ofertat = null: поле пустое или значение не из ✅DA/❌NU; офертой не считается.
    lead_frame["is_ofertat"] = lead_frame["ofertat"].astype("boolean").fillna(False).astype(bool)
    showroom_visit_sources = config.status_mapping.sources["showroom_visit"]
    lead_frame["is_showroom_visit"] = lead_frame["source_name"].isin(showroom_visit_sources)
    return lead_frame
