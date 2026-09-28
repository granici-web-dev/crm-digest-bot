import pandas as pd

from digest.config import AppConfig, TimeSettings
from digest.metrics.daily import daily_window, daily_window_days


def own_daily_window_start(created_at: pd.Series, time_settings: TimeSettings) -> pd.Series:
    days = daily_window_days(created_at, time_settings)
    starts = {day: daily_window(day, time_settings).start for day in set(days)}
    window_start: pd.Series = pd.to_datetime(days.map(starts), utc=True).dt.tz_convert(
        time_settings.timezone
    )
    return window_start


def earliest_contact_created_at(lead_frame: pd.DataFrame) -> pd.Series:
    # Совпадение контакта: равен непустой ключ телефона или e-mail (digest.contact_keys).
    # Сравнение внутри одного снапшота: истории ключей не нужно, смена секрета ничего не ломает.
    created_at = lead_frame["created_at"]
    by_key = [
        created_at.groupby(lead_frame[key_column]).transform("min")
        for key_column in ("contact_phone_key", "contact_email_key")
    ]
    earliest: pd.Series = pd.concat(by_key, axis=1).min(axis=1)
    return earliest


def showroom_visit_flags(lead_frame: pd.DataFrame, config: AppConfig) -> pd.DataFrame:
    # Одно правило визита для d1, w2, KPI и чата (ADR-007, docs/kpi-definitions.md, «Визит»).
    # Revenire считается от начала собственного ежедневного окна лида, а не периода отчёта:
    # тогда неделя и месяц по построению равны сумме дней d1. Лид без ключей контакта
    # (снапшоты до миграции 0005) revenire не бывает.
    time_settings = config.status_mapping.time
    is_showroom_source = lead_frame["source_name"].isin(
        config.status_mapping.sources.showroom_visit
    )
    # sources.partner не пересекается с showroom_visit (валидатор групп), партнёр виден
    # только по категории.
    counted_source = is_showroom_source & lead_frame["category"].ne("PARTNERSHIP")
    window_start = own_daily_window_start(lead_frame["created_at"], time_settings)
    has_earlier_contact = earliest_contact_created_at(lead_frame).lt(window_start)
    is_showroom_revenire = counted_source & has_earlier_contact
    return pd.DataFrame(
        {
            "is_showroom_source": is_showroom_source,
            "is_showroom_revenire": is_showroom_revenire,
            "is_showroom_visit": counted_source & ~is_showroom_revenire,
        },
        index=lead_frame.index,
    )
