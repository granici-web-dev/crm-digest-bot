from collections.abc import Hashable, Mapping
from dataclasses import dataclass, fields
from datetime import date, datetime
from typing import Any

import pandas as pd

from digest.config import AppConfig


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class LeadCounts:
    leads: int
    irr_leads: int
    useful: int
    clienti: int
    offers: int
    nar: int
    buget: int
    pnp: int
    showroom_visits: int
    clienti_from_showroom: int
    active_offers_14: int
    unmapped: int


@dataclass(frozen=True)
class Kpis:
    scr: float | None
    l2o: float | None
    o2c: float | None
    cdr: float | None
    plr: float | None
    sc: float | None
    pfr: float | None
    acr: float | None
    irr: float | None


COUNT_NAMES = tuple(field.name for field in fields(LeadCounts))


def count_flags(
    lead_frame: pd.DataFrame, period: Period, analysis_date: date, config: AppConfig
) -> pd.DataFrame:
    # docs/kpi-definitions.md, «Базовые множества»: период по created_at, [start, end).
    created_at = lead_frame["created_at"]
    in_period = created_at.ge(period.start) & created_at.lt(period.end)
    leads = lead_frame[in_period & ~lead_frame["is_excluded_from_leads"]]

    # docs/kpi-definitions.md, «Базовые множества», ACTIVE_OFFERS_14: день контакта по Бухаресту,
    # строго больше active_offer_stale_days; last_contact_at = null оферту висящей не делает.
    last_contact_at = leads["last_contact_at"]
    last_contact_day = last_contact_at.dt.tz_localize(None).dt.normalize()
    days_since_contact = (pd.Timestamp(analysis_date) - last_contact_day).dt.days
    stale_contact = last_contact_at.notna() & days_since_contact.gt(
        config.kpi.active_offer_stale_days
    )

    clienti, irelevant = leads["is_clienti"], leads["is_irelevant"]
    offers, showroom_visits = leads["is_ofertat"], leads["is_showroom_visit"]
    flags = pd.DataFrame(
        {
            "leads": True,
            "irr_leads": irelevant,
            "useful": ~leads["is_excluded_from_useful"],
            "clienti": clienti,
            "offers": offers,
            "nar": leads["is_nu_a_raspuns"],
            "buget": leads["is_buget"],
            "pnp": leads["is_produs_nepotrivit"],
            "showroom_visits": showroom_visits,
            "clienti_from_showroom": clienti & showroom_visits,
            "active_offers_14": offers & ~clienti & ~irelevant & stale_contact,
            # Инвариант 4: UNMAPPED входит в LEADS и USEFUL и отдельно считается здесь.
            "unmapped": leads["is_unmapped"],
        },
        index=leads.index,
    )
    flags["assigned_to_id"] = leads["assigned_to_id"]
    flags["showroom"] = leads["showroom"]
    return flags


def counts_from_sums(sums: Mapping[Hashable, Any]) -> LeadCounts:
    return LeadCounts(**{name: int(sums[name]) for name in COUNT_NAMES})


def sum_counts(flags: pd.DataFrame) -> LeadCounts:
    return counts_from_sums(flags[list(COUNT_NAMES)].sum().to_dict())


def lead_counts(
    lead_frame: pd.DataFrame, period: Period, analysis_date: date, config: AppConfig
) -> LeadCounts:
    return sum_counts(count_flags(lead_frame, period, analysis_date, config))


def lead_counts_by_showroom(
    lead_frame: pd.DataFrame, period: Period, analysis_date: date, config: AppConfig
) -> dict[str | None, LeadCounts]:
    flags = count_flags(lead_frame, period, analysis_date, config)
    showroom = flags["showroom"]
    # Значение вне списка showrooms тоже получает строку: лиды не пропадают из разреза молча.
    observed = set(showroom.dropna()) - set(config.status_mapping.showrooms)
    keys: list[str | None] = [*config.status_mapping.showrooms, *sorted(observed), None]
    return {
        key: sum_counts(flags[showroom.isna() if key is None else showroom.eq(key)]) for key in keys
    }


def lead_counts_by_manager(
    lead_frame: pd.DataFrame, period: Period, analysis_date: date, config: AppConfig
) -> dict[int, LeadCounts]:
    active_ids = [manager.id for manager in config.managers.managers if manager.active]
    flags = count_flags(lead_frame, period, analysis_date, config)
    sums = flags.groupby("assigned_to_id")[list(COUNT_NAMES)].sum()
    sums_by_manager = sums.reindex(active_ids, fill_value=0).to_dict("index")
    return {manager_id: counts_from_sums(sums_by_manager[manager_id]) for manager_id in active_ids}


def ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def kpis_from(counts: LeadCounts) -> Kpis:
    # Формулы: docs/kpi-definitions.md, «KPI». Деление на ноль → None, в отчёте «—».
    return Kpis(
        scr=ratio(counts.clienti, counts.useful),
        l2o=ratio(counts.offers, counts.useful),
        o2c=ratio(counts.clienti, counts.offers),
        cdr=ratio(counts.leads - counts.nar, counts.leads),
        plr=ratio(counts.buget, counts.leads - counts.irr_leads - counts.nar),
        sc=ratio(counts.clienti_from_showroom, counts.showroom_visits),
        pfr=ratio(counts.pnp, counts.useful),
        acr=ratio(counts.active_offers_14, counts.leads),
        irr=ratio(counts.irr_leads, counts.leads),
    )
