from dataclasses import dataclass

import pandas as pd

from digest.config import AppConfig
from digest.metrics.kpi import Period

LEAD_ROWS = ("leads_web", "leads_phone", "leads_whatsapp", "leads_partner", "leads_other")
TRANSITION_ROWS = ("showroom_visits", "offers", "contracts")


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


def lead_row_flags(today_frame: pd.DataFrame, period: Period, config: AppConfig) -> pd.DataFrame:
    # Строки Leads как в ручном отчёте продавцов (docs/shapes/2026-09-25-delivery.md, «d1: счёт»):
    # взаимоисключающие, по created_at в окне. Лид с источником Showroom вне ACTIVE_FOLLOWUP
    # это визит, а не лид. Источник вне всех групп попадает в Alte, чтобы не пропасть молча.
    sources = config.status_mapping.sources
    source = today_frame["source_name"]
    category = today_frame["category"]
    created_at = today_frame["created_at"]
    in_window = created_at.ge(period.start) & created_at.lt(period.end)
    is_partner = category.eq("PARTNERSHIP") | source.isin(sources.partner)
    is_showroom_source = source.isin(sources.showroom_visit)
    is_web, is_phone = source.isin(sources.web), source.isin(sources.phone)
    is_whatsapp = source.isin(sources.whatsapp)
    is_other = (is_showroom_source & category.eq("ACTIVE_FOLLOWUP")) | ~(
        is_showroom_source | is_web | is_phone | is_whatsapp
    )
    counted = in_window & ~is_partner
    return pd.DataFrame(
        {
            "leads_partner": in_window & is_partner,
            "leads_other": counted & is_other,
            "leads_web": counted & ~is_other & is_web,
            "leads_phone": counted & ~is_other & is_phone,
            "leads_whatsapp": counted & ~is_other & is_whatsapp,
        }
    )


def transition_flags(
    today_frame: pd.DataFrame, previous_frame: pd.DataFrame, config: AppConfig
) -> pd.DataFrame:
    # status_changed_at в mefi хранит только последнее изменение (CLAUDE.md, инвариант 3):
    # переход в визит, оферту, контракт виден только как разница двух снапшотов.
    sources = config.status_mapping.sources
    previous = previous_frame.set_index("lead_id")
    lead_id = today_frame["lead_id"]
    is_new = ~lead_id.isin(previous.index)
    previous_status = lead_id.map(previous["status_name"])
    was_ofertat = lead_id.map(previous["is_ofertat"]).fillna(False).astype(bool)
    was_clienti = lead_id.map(previous["is_clienti"]).fillna(False).astype(bool)
    became_visit_status = today_frame["status_name"].eq(
        sources.showroom_visit_status
    ) & previous_status.ne(sources.showroom_visit_status)
    return pd.DataFrame(
        {
            "showroom_visits": (is_new & today_frame["is_showroom_visit"]) | became_visit_status,
            "offers": today_frame["is_ofertat"] & ~was_ofertat,
            "contracts": today_frame["is_clienti"] & ~was_clienti,
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


def seller_format_counts(
    today_frame: pd.DataFrame,
    previous_frame: pd.DataFrame | None,
    period: Period,
    config: AppConfig,
) -> SellerFormatCounts:
    flags = lead_row_flags(today_frame, period, config)
    has_previous_snapshot = previous_frame is not None
    if previous_frame is not None:
        flags = flags.join(transition_flags(today_frame, previous_frame, config))
    else:
        flags = flags.assign(**dict.fromkeys(TRANSITION_ROWS, False))
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
    )
