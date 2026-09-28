from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from digest.config import AppConfig, TimeSettings
from digest.metrics.kpi import Period

LEAD_ROWS = ("leads_web", "leads_phone", "leads_whatsapp", "leads_partner", "leads_other")


@dataclass(frozen=True)
class PreviousSnapshot:
    snapshot_date: date
    frame: pd.DataFrame


@dataclass(frozen=True)
class SellerFormatRow:
    leads_web: int
    leads_phone: int
    leads_whatsapp: int
    leads_partner: int
    leads_other: int
    showroom_visits: int
    offers: int | None
    contracts: int | None

    @property
    def leads(self) -> int:
        return (
            self.leads_web
            + self.leads_phone
            + self.leads_whatsapp
            + self.leads_partner
            + self.leads_other
        )


@dataclass(frozen=True)
class SellerFormatCounts:
    by_showroom: dict[str, SellerFormatRow]
    without_showroom_count: int
    total: SellerFormatRow
    has_previous_snapshot: bool
    has_clients_snapshot: bool
    unknown_source_lead_ids: tuple[int, ...]
    missing_from_previous_lead_ids: tuple[int, ...]


def daily_window(report_date: date, time_settings: TimeSettings) -> Period:
    # Окно отчёта продавцов 19:00 вчера → 19:00 сегодня по Бухаресту (CLAUDE.md, «Окна времени»).
    timezone = ZoneInfo(time_settings.timezone)
    window_end = time_settings.daily_window_end
    return Period(
        datetime.combine(report_date - timedelta(days=1), window_end, tzinfo=timezone),
        datetime.combine(report_date, window_end, tzinfo=timezone),
    )


def matches_contact_created_before(lead_frame: pd.DataFrame, moment: datetime) -> pd.Series:
    # Совпадение контакта: равен непустой ключ телефона или e-mail (digest.contact_keys).
    # Сравнение внутри одного снапшота: истории ключей не нужно, смена секрета ничего не ломает.
    older = lead_frame[lead_frame["created_at"].lt(moment)]
    older_phone_keys = set(older["contact_phone_key"].dropna())
    older_email_keys = set(older["contact_email_key"].dropna())
    return lead_frame["contact_phone_key"].isin(older_phone_keys) | lead_frame[
        "contact_email_key"
    ].isin(older_email_keys)


def lead_row_flags(today_frame: pd.DataFrame, period: Period, config: AppConfig) -> pd.DataFrame:
    # Строки d1 по правилам владельца 28.09.2026 (ADR-006): взаимоисключающие, по created_at
    # в окне. Лид с источником Showroom это визит, если контакт не встречался у лида, созданного
    # раньше начала окна, и revenire (Alte), если встречался. Источник вне всех групп попадает
    # в Alte и в алерт: не пропадает молча.
    sources = config.status_mapping.sources
    source = today_frame["source_name"]
    created_at = today_frame["created_at"]
    in_window = created_at.ge(period.start) & created_at.lt(period.end)
    is_partner = today_frame["category"].eq("PARTNERSHIP") | source.isin(sources.partner)
    is_showroom_source = source.isin(sources.showroom_visit)
    is_revenire = is_showroom_source & matches_contact_created_before(today_frame, period.start)
    is_web, is_phone = source.isin(sources.web), source.isin(sources.phone)
    is_whatsapp = source.isin(sources.whatsapp)
    is_unknown_source = ~source.isin(
        [
            *sources.showroom_visit,
            *sources.web,
            *sources.phone,
            *sources.whatsapp,
            *sources.partner,
            *sources.other,
            *sources.repeat_client,
        ]
    )
    # Повторный клиент по источнику (m11) в d1 обычный лид строки Alte, как sources.other.
    is_other_source = source.isin([*sources.other, *sources.repeat_client])
    is_other = is_revenire | is_other_source | is_unknown_source
    counted = in_window & ~is_partner
    return pd.DataFrame(
        {
            "leads_partner": in_window & is_partner,
            "leads_other": counted & is_other,
            "leads_web": counted & ~is_other & is_web,
            "leads_phone": counted & ~is_other & is_phone,
            "leads_whatsapp": counted & ~is_other & is_whatsapp,
            "showroom_visits": counted & is_showroom_source & ~is_revenire,
            "unknown_source": counted & is_unknown_source,
        }
    )


def transition_flags(
    today_frame: pd.DataFrame, previous_frame: pd.DataFrame, period: Period
) -> pd.DataFrame:
    # status_changed_at в mefi хранит только последнее изменение (CLAUDE.md, инвариант 3):
    # переход в оферту или в PARTNERSHIP виден только как разница двух снапшотов.
    # Новый лид создан не раньше предыдущего снапшота (начало окна). Лид старше, которого вчера
    # не было (например, пропущен как битый), в разницу не входит: его статус не переход за день.
    previous = previous_frame.set_index("lead_id")
    lead_id = today_frame["lead_id"]
    created_at = today_frame["created_at"]
    in_previous = lead_id.isin(previous.index)
    is_new = ~in_previous & created_at.ge(period.start)
    was_ofertat = lead_id.map(previous["is_ofertat"]).eq(True)
    was_partnership = lead_id.map(previous["category"]).eq("PARTNERSHIP")
    # Лид, созданный в окне, уже посчитан как новый: его переход второй раз не считается.
    created_before_window = created_at.lt(period.start)
    return pd.DataFrame(
        {
            "offers": today_frame["is_ofertat"] & (is_new | (in_previous & ~was_ofertat)),
            "partner_transitions": in_previous
            & created_before_window
            & today_frame["category"].eq("PARTNERSHIP")
            & ~was_partnership,
            "missing_from_previous": ~in_previous & ~is_new,
        }
    )


def new_client_flags(clients_frame: pd.DataFrame, period: Period, config: AppConfig) -> pd.Series:
    # Contract Cantitate = новые клиенты mefi за окно (решение владельца 28.09.2026, ADR-006).
    # Клиенты до contracts_count_from это ручной ввод старых договоров, не считаются никогда.
    client_settings = config.status_mapping.clients
    count_from = datetime.combine(
        client_settings.contracts_count_from,
        time(),
        tzinfo=ZoneInfo(config.status_mapping.time.timezone),
    )
    created_at = clients_frame["created_at"]
    return created_at.ge(period.start) & created_at.lt(period.end) & created_at.ge(count_from)


def row_from(
    flags: pd.DataFrame, contracts: int | None, has_previous_snapshot: bool
) -> SellerFormatRow:
    return SellerFormatRow(
        leads_web=int(flags["leads_web"].sum()),
        leads_phone=int(flags["leads_phone"].sum()),
        leads_whatsapp=int(flags["leads_whatsapp"].sum()),
        leads_partner=int(flags["leads_partner"].sum()),
        leads_other=int(flags["leads_other"].sum()),
        showroom_visits=int(flags["showroom_visits"].sum()),
        offers=int(flags["offers"].sum()) if has_previous_snapshot else None,
        contracts=contracts,
    )


def flagged_lead_ids(today_frame: pd.DataFrame, flag: pd.Series) -> tuple[int, ...]:
    return tuple(sorted(int(lead_id) for lead_id in today_frame.loc[flag, "lead_id"]))


def comparable_previous(
    previous: PreviousSnapshot | None, report_date: date
) -> PreviousSnapshot | None:
    # Разница с более старым снапшотом покрыла бы несколько дней под подписью одного
    # (docs/shapes/2026-09-25-delivery.md, «d1: счёт»). Правило общее для d1 и d4.
    if previous is not None and previous.snapshot_date == report_date - timedelta(days=1):
        return previous
    return None


def seller_format_counts(
    today_frame: pd.DataFrame,
    previous: PreviousSnapshot | None,
    clients_frame: pd.DataFrame | None,
    report_date: date,
    config: AppConfig,
) -> SellerFormatCounts:
    period = daily_window(report_date, config.status_mapping.time)
    flags = lead_row_flags(today_frame, period, config)
    comparable = comparable_previous(previous, report_date)
    if comparable is not None:
        transitions = transition_flags(today_frame, comparable.frame, period)
        # Без вчерашнего снапшота строка Designer/Colaboratori считает только новые лиды,
        # а не «—»: продавцы привыкли видеть в ней число (ответ пользователя 28.09.2026).
        flags["leads_partner"] |= transitions["partner_transitions"]
        flags = flags.join(transitions[["offers", "missing_from_previous"]])
    else:
        flags = flags.assign(offers=False, missing_from_previous=False)
    has_previous_snapshot = comparable is not None
    showroom = today_frame["showroom"]
    # Шоурум каждого нового клиента окна; None, если снапшота клиентов за дату нет.
    new_client_showrooms = (
        None
        if clients_frame is None
        else clients_frame.loc[new_client_flags(clients_frame, period, config), "showroom"]
    )

    def contracts_in(showroom_name: str) -> int | None:
        if new_client_showrooms is None:
            return None
        return int(new_client_showrooms.eq(showroom_name).sum())

    # Шоурум вне списка showrooms получает свой блок: лиды не пропадают молча (как в kpi.py).
    observed_showrooms = set(showroom.dropna())
    if new_client_showrooms is not None:
        observed_showrooms |= set(new_client_showrooms.dropna())
    by_showroom = {
        name: row_from(flags[showroom.eq(name)], contracts_in(name), has_previous_snapshot)
        for name in [
            *config.status_mapping.showrooms,
            *sorted(observed_showrooms - set(config.status_mapping.showrooms)),
        ]
    }
    counted_anywhere = flags[[*LEAD_ROWS, "showroom_visits", "offers"]].any(axis=1)
    without_showroom_count = int((counted_anywhere & showroom.isna()).sum())
    if new_client_showrooms is not None:
        without_showroom_count += int(new_client_showrooms.isna().sum())
    return SellerFormatCounts(
        by_showroom=by_showroom,
        without_showroom_count=without_showroom_count,
        total=row_from(
            flags,
            None if new_client_showrooms is None else len(new_client_showrooms),
            has_previous_snapshot,
        ),
        has_previous_snapshot=has_previous_snapshot,
        has_clients_snapshot=clients_frame is not None,
        unknown_source_lead_ids=flagged_lead_ids(today_frame, flags["unknown_source"]),
        missing_from_previous_lead_ids=flagged_lead_ids(
            today_frame, flags["missing_from_previous"]
        ),
    )
