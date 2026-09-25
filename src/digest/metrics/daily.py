from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from digest.config import AppConfig, TimeSettings
from digest.metrics.kpi import Period

LEAD_ROWS = ("leads_web", "leads_phone", "leads_whatsapp", "leads_partner", "leads_other")
TRANSITION_ROWS = ("showroom_visits", "offers", "contracts")


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
    showroom_visits: int | None
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
    without_showroom_lead_count: int
    total: SellerFormatRow
    has_previous_snapshot: bool
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


def lead_row_flags(today_frame: pd.DataFrame, period: Period, config: AppConfig) -> pd.DataFrame:
    # Строки Leads как в ручном отчёте продавцов (docs/shapes/2026-09-25-delivery.md, «d1: счёт»):
    # взаимоисключающие, по created_at в окне. Лид с источником Showroom вне ACTIVE_FOLLOWUP
    # это визит, а не лид. Источник вне всех групп попадает в Alte и в алерт: не пропадает молча.
    sources = config.status_mapping.sources
    source = today_frame["source_name"]
    category = today_frame["category"]
    created_at = today_frame["created_at"]
    in_window = created_at.ge(period.start) & created_at.lt(period.end)
    is_partner = category.eq("PARTNERSHIP") | source.isin(sources.partner)
    is_showroom_source = source.isin(sources.showroom_visit)
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
        ]
    )
    is_other = (
        (is_showroom_source & category.eq("ACTIVE_FOLLOWUP"))
        | source.isin(sources.other)
        | is_unknown_source
    )
    counted = in_window & ~is_partner
    return pd.DataFrame(
        {
            "leads_partner": in_window & is_partner,
            "leads_other": counted & is_other,
            "leads_web": counted & ~is_other & is_web,
            "leads_phone": counted & ~is_other & is_phone,
            "leads_whatsapp": counted & ~is_other & is_whatsapp,
            "unknown_source": counted & is_unknown_source,
        }
    )


def transition_flags(
    today_frame: pd.DataFrame, previous_frame: pd.DataFrame, period: Period, config: AppConfig
) -> pd.DataFrame:
    # status_changed_at в mefi хранит только последнее изменение (CLAUDE.md, инвариант 3):
    # переход в визит, оферту, контракт виден только как разница двух снапшотов.
    # Новый лид создан не раньше предыдущего снапшота (начало окна). Лид старше, которого вчера
    # не было (например, пропущен как битый), в разницу не входит: его статус не переход за день.
    sources = config.status_mapping.sources
    previous = previous_frame.set_index("lead_id")
    lead_id = today_frame["lead_id"]
    in_previous = lead_id.isin(previous.index)
    is_new = ~in_previous & today_frame["created_at"].ge(period.start)
    is_visit_status = today_frame["status_name"].eq(sources.showroom_visit_status)
    was_visit_status = lead_id.map(previous["status_name"]).eq(sources.showroom_visit_status)
    was_ofertat = lead_id.map(previous["is_ofertat"]).eq(True)
    was_clienti = lead_id.map(previous["is_clienti"]).eq(True)
    return pd.DataFrame(
        {
            "showroom_visits": (is_new & (today_frame["is_showroom_visit"] | is_visit_status))
            | (in_previous & is_visit_status & ~was_visit_status),
            "offers": today_frame["is_ofertat"] & (is_new | (in_previous & ~was_ofertat)),
            "contracts": today_frame["is_clienti"] & (is_new | (in_previous & ~was_clienti)),
            "missing_from_previous": ~in_previous & ~is_new,
        }
    )


def row_from(flags: pd.DataFrame, has_previous_snapshot: bool) -> SellerFormatRow:
    def transition_count(name: str) -> int | None:
        return int(flags[name].sum()) if has_previous_snapshot else None

    return SellerFormatRow(
        leads_web=int(flags["leads_web"].sum()),
        leads_phone=int(flags["leads_phone"].sum()),
        leads_whatsapp=int(flags["leads_whatsapp"].sum()),
        leads_partner=int(flags["leads_partner"].sum()),
        leads_other=int(flags["leads_other"].sum()),
        showroom_visits=transition_count("showroom_visits"),
        offers=transition_count("offers"),
        contracts=transition_count("contracts"),
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
    report_date: date,
    config: AppConfig,
) -> SellerFormatCounts:
    period = daily_window(report_date, config.status_mapping.time)
    flags = lead_row_flags(today_frame, period, config)
    comparable = comparable_previous(previous, report_date)
    if comparable is not None:
        flags = flags.join(transition_flags(today_frame, comparable.frame, period, config))
    else:
        flags = flags.assign(**dict.fromkeys([*TRANSITION_ROWS, "missing_from_previous"], False))
    has_previous_snapshot = comparable is not None
    showroom = today_frame["showroom"]
    # Шоурум вне списка showrooms получает свой блок: лиды не пропадают молча (как в kpi.py).
    observed = sorted(set(showroom.dropna()) - set(config.status_mapping.showrooms))
    by_showroom = {
        name: row_from(flags[showroom.eq(name)], has_previous_snapshot)
        for name in [*config.status_mapping.showrooms, *observed]
    }
    counted_anywhere = flags[[*LEAD_ROWS, *TRANSITION_ROWS]].any(axis=1)
    return SellerFormatCounts(
        by_showroom=by_showroom,
        without_showroom_lead_count=int((counted_anywhere & showroom.isna()).sum()),
        total=row_from(flags, has_previous_snapshot),
        has_previous_snapshot=has_previous_snapshot,
        unknown_source_lead_ids=flagged_lead_ids(today_frame, flags["unknown_source"]),
        missing_from_previous_lead_ids=flagged_lead_ids(
            today_frame, flags["missing_from_previous"]
        ),
    )
