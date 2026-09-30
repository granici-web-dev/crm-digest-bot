from typing import Any

import pandas as pd

from digest.config import AppConfig
from digest.metrics.visits import showroom_visit_flags

LEAD_FRAME_COLUMNS = (
    "lead_id",
    "category",
    "loss_reason",
    "status_name",
    "source_name",
    "utm_campanie",
    "showroom",
    "ofertat",
    "data_revenire",
    "data_revenire_problem",
    "is_duplicate",
    "assigned_to_id",
    "assigned_to_name",
    "created_by_id",
    "created_at",
    "status_changed_at",
    "last_contact_at",
    "converted_at",
    "contact_phone_key",
    "contact_email_key",
)
CLIENT_FRAME_COLUMNS = ("client_id", "created_at", "showroom")
TIMESTAMP_COLUMNS = ("created_at", "status_changed_at", "last_contact_at", "converted_at")


def prepare_lead_frame(rows: list[dict[str, Any]], config: AppConfig) -> pd.DataFrame:
    lead_frame = pd.DataFrame.from_records(rows, columns=list(LEAD_FRAME_COLUMNS))
    lead_frame["assigned_to_id"] = lead_frame["assigned_to_id"].astype("Int64")
    lead_frame["created_by_id"] = lead_frame["created_by_id"].astype("Int64")
    test_account_ids = [manager.id for manager in config.managers.managers if manager.test_account]
    # Лиды тестовых аккаунтов вне всех метрик (docs/kpi-definitions.md, «Базовые множества»).
    lead_frame = lead_frame[~lead_frame["assigned_to_id"].isin(test_account_ids)]
    lead_frame = lead_frame.reset_index(drop=True)

    timezone = config.status_mapping.time.timezone
    for column in TIMESTAMP_COLUMNS:
        # utc=True молча считал бы naive-время UTC и сдвигал окна на 2–3 часа.
        if any(value.tzinfo is None for value in lead_frame[column].dropna()):
            raise ValueError(f"{column}: время без таймзоны, окна отчётов посчитать нельзя")
        lead_frame[column] = pd.to_datetime(lead_frame[column], utc=True).dt.tz_convert(timezone)
    # data_revenire в mefi дата без времени (date_picker): сравнивается с календарным днём.
    lead_frame["data_revenire"] = pd.to_datetime(lead_frame["data_revenire"])

    # Флаги только из category, loss_reason и конфига: статусы mefi живут в status-mapping.yaml.
    categories = config.status_mapping.categories
    category, loss_reason = lead_frame["category"], lead_frame["loss_reason"]
    reasons_excluded_from_useful = [
        reason_name
        for reason_name, reason in categories.LOST.reasons.items()
        if reason.excluded_from_useful
    ]
    lead_frame["is_clienti"] = category.eq("WON")
    lead_frame["is_unmapped"] = category.eq("UNMAPPED")
    lead_frame["is_excluded_from_leads"] = category.eq("PARTNERSHIP") & (
        categories.PARTNERSHIP.excluded_from_leads
    )
    lead_frame["is_excluded_from_useful"] = loss_reason.isin(reasons_excluded_from_useful)
    lead_frame["is_irelevant"] = loss_reason.eq("IRELEVANT")
    # Статусы, у которых продавец обязан вести Data revenire: Revenire 1/2/3 и причины LOST с
    # followup_field (Stand BY, бриф §3).
    followup_reasons = [
        reason_name
        for reason_name, reason in categories.LOST.reasons.items()
        if reason.followup_field is not None
    ]
    lead_frame["is_followup_status"] = category.eq("ACTIVE_FOLLOWUP") | (
        category.eq("LOST") & loss_reason.isin(followup_reasons)
    )
    lead_frame["is_nu_a_raspuns"] = loss_reason.eq("NU_RASPUNS")
    lead_frame["is_buget"] = loss_reason.eq("BUGET")
    lead_frame["is_produs_nepotrivit"] = loss_reason.eq("PRODUS_NEPOTRIVIT")
    # ofertat = null: поле пустое или значение не из ✅DA/❌NU; офертой не считается.
    lead_frame["is_ofertat"] = lead_frame["ofertat"].astype("boolean").fillna(False).astype(bool)
    return lead_frame.join(showroom_visit_flags(lead_frame, config))


def status_set_at(lead_frame: pd.DataFrame) -> pd.Series:
    # status_changed_at = null, если статус задан при создании (CLAUDE.md, «Ловушки mefi API»):
    # тогда статус стоит с created_at.
    return lead_frame["status_changed_at"].fillna(lead_frame["created_at"])


def unknown_manager_ids(lead_frame: pd.DataFrame, config: AppConfig) -> set[int]:
    known_ids = {manager.id for manager in config.managers.managers}
    # created_by тоже: лид, заведённый продавцом вне managers.yaml, d2 молча показал бы
    # нетронутым.
    referenced_ids = pd.concat([lead_frame["assigned_to_id"], lead_frame["created_by_id"]])
    return {int(manager_id) for manager_id in referenced_ids.dropna().unique()} - known_ids


def prepare_client_frame(rows: list[dict[str, Any]], config: AppConfig) -> pd.DataFrame:
    client_frame = pd.DataFrame.from_records(rows, columns=list(CLIENT_FRAME_COLUMNS))
    # Как у лидов: naive-время utc=True сдвинуло бы окно Contract Cantitate на 2–3 часа.
    if any(value.tzinfo is None for value in client_frame["created_at"].dropna()):
        raise ValueError("created_at клиента: время без таймзоны, окна отчётов посчитать нельзя")
    client_frame["created_at"] = pd.to_datetime(client_frame["created_at"], utc=True).dt.tz_convert(
        config.status_mapping.time.timezone
    )
    return client_frame
