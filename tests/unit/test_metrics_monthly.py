from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from digest.config import AppConfig
from digest.metrics.daily import daily_window
from digest.metrics.frame import prepare_lead_frame
from digest.metrics.kpi import Period
from digest.metrics.monthly import (
    BELOW_ALL_LEVELS,
    ScrRow,
    month_window,
    monthly_funnel,
    monthly_lead_rows,
    monthly_loss_reasons,
    monthly_scr,
    monthly_trend,
    scr_level,
    trend_months,
)
from digest.metrics.weekly import LEAD_ROW_COLUMNS
from factories import BUCHAREST, make_snapshot_row

SEPTEMBER_END = date(2026, 9, 30)
UTC = ZoneInfo("UTC")


def at(day: date, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=BUCHAREST)


def frame(app_config: AppConfig, *rows: dict[str, Any]) -> pd.DataFrame:
    return prepare_lead_frame(list(rows), app_config)


def lead(lead_id: int, created_at: datetime, **overrides: Any) -> dict[str, Any]:
    return make_snapshot_row(
        **{"lead_id": lead_id, "created_at": created_at, "last_contact_at": created_at, **overrides}
    )


def lost(lead_id: int, created_at: datetime, reason: str, **overrides: Any) -> dict[str, Any]:
    return lead(lead_id, created_at, category="LOST", loss_reason=reason, **overrides)


def test_month_window_is_daily_windows_of_every_day_in_month(app_config: AppConfig) -> None:
    time_settings = app_config.status_mapping.time

    window = month_window(SEPTEMBER_END, time_settings)

    assert window == Period(at(date(2026, 8, 31), 19), at(SEPTEMBER_END, 19))
    assert window.start == daily_window(date(2026, 9, 1), time_settings).start
    assert month_window(date(2026, 9, 12), time_settings) == window


@pytest.mark.parametrize(
    ("report_date", "start", "end"),
    [
        (date(2026, 3, 31), datetime(2026, 2, 28, 17, 0), datetime(2026, 3, 31, 16, 0)),
        (date(2026, 10, 31), datetime(2026, 9, 30, 16, 0), datetime(2026, 10, 31, 17, 0)),
        (date(2027, 1, 31), datetime(2026, 12, 31, 17, 0), datetime(2027, 1, 31, 17, 0)),
    ],
)
def test_month_window_ends_at_1900_bucharest_across_dst(
    app_config: AppConfig, report_date: date, start: datetime, end: datetime
) -> None:
    window = month_window(report_date, app_config.status_mapping.time)

    assert window.start.astimezone(UTC).replace(tzinfo=None) == start
    assert window.end.astimezone(UTC).replace(tzinfo=None) == end


def test_trend_months_are_six_months_ending_with_report_month_across_year() -> None:
    assert trend_months(date(2027, 2, 28)) == (
        date(2026, 9, 1),
        date(2026, 10, 1),
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
        date(2027, 2, 1),
    )


def test_monthly_funnel_counts_leads_in_month_window_by_showroom(app_config: AppConfig) -> None:
    leads = frame(
        app_config,
        lead(1, at(date(2026, 8, 31), 19), showroom="Brașov"),
        lead(2, at(date(2026, 9, 10), 12), showroom="Brașov", ofertat=True),
        lead(3, at(date(2026, 9, 10), 13), showroom="Cluj", category="WON", ofertat=True),
        lead(4, at(date(2026, 9, 11), 12), showroom="Iași"),
        lead(5, at(date(2026, 9, 12), 12), showroom=None, category="LOST", loss_reason="IRELEVANT"),
        lead(6, at(date(2026, 9, 13), 12), category="PARTNERSHIP", status_name="DESIGNER"),
        lead(7, at(SEPTEMBER_END, 18, 59, 59), showroom="Cluj"),
        lead(8, at(date(2026, 8, 31), 18, 59, 59), showroom="Brașov"),
        lead(9, at(SEPTEMBER_END, 19), showroom="Brașov"),
    )

    funnel = monthly_funnel(leads, SEPTEMBER_END, app_config)

    company = funnel.company
    assert funnel.month == date(2026, 9, 1)
    assert (company.leads, company.useful, company.offers, company.clienti) == (6, 5, 2, 1)
    assert list(funnel.by_showroom) == ["Brașov", "București", "Cluj", "Iași", None]
    assert funnel.by_showroom["Brașov"].leads == 2
    assert funnel.by_showroom["Cluj"].leads == 2
    assert funnel.by_showroom["Iași"].leads == 1
    assert funnel.by_showroom[None].useful == 0
    assert sum(counts.leads for counts in funnel.by_showroom.values()) == company.leads
    assert funnel.showrooms_with_leads == ("Brașov", "Cluj", "Iași", None)
    assert funnel.named_showrooms_with_leads == ("Brașov", "Cluj", "Iași")
    assert funnel.without_showroom == funnel.by_showroom[None]
    assert (funnel.without_showroom.leads, funnel.without_showroom.irr_leads) == (1, 1)


@pytest.mark.parametrize(
    ("scr", "expected_level"),
    [
        (0.10, "scr_elite"),
        (0.0999, "scr_bine"),
        (0.07, "scr_bine"),
        (0.05, "scr_minim"),
        (0.0499, BELOW_ALL_LEVELS),
        (0.0, BELOW_ALL_LEVELS),
        (None, None),
    ],
)
def test_scr_level_is_first_threshold_met_inclusive(
    app_config: AppConfig, scr: float | None, expected_level: str | None
) -> None:
    assert scr_level(scr, app_config) == expected_level


def test_monthly_scr_by_showroom_and_company_with_target(app_config: AppConfig) -> None:
    day = at(date(2026, 9, 10), 12)
    rows = [lead(lead_id, day, showroom="Brașov") for lead_id in range(1, 10)]
    rows.append(lead(10, day, showroom="Brașov", category="WON"))
    rows += [lead(lead_id, day, showroom="Cluj") for lead_id in range(11, 30)]
    rows.append(lead(30, day, showroom="Cluj", category="WON"))
    leads = frame(app_config, *rows)

    scr = monthly_scr(monthly_funnel(leads, SEPTEMBER_END, app_config), app_config)

    assert scr.by_showroom["Brașov"].scr == pytest.approx(0.10)
    assert (scr.by_showroom["Brașov"].level, scr.by_showroom["Brașov"].meets_target) == (
        "scr_elite",
        True,
    )
    assert scr.by_showroom["Cluj"].scr == pytest.approx(0.05)
    assert (scr.by_showroom["Cluj"].level, scr.by_showroom["Cluj"].meets_target) == (
        "scr_minim",
        False,
    )
    assert scr.by_showroom["București"] == ScrRow(None, None, None)
    assert scr.company.scr == pytest.approx(2 / 30)
    assert scr.company.level == "scr_minim"


def test_monthly_trend_counts_leads_by_created_at_and_contracts_by_converted_at(
    app_config: AppConfig,
) -> None:
    leads = frame(
        app_config,
        lead(1, at(date(2026, 4, 1), 12)),
        lead(2, at(date(2026, 4, 30), 18), category="WON", converted_at=at(date(2026, 9, 2), 12)),
        lead(3, at(date(2026, 9, 5), 12), category="WON", converted_at=at(date(2026, 9, 6), 12)),
        lead(4, at(date(2026, 8, 31), 20)),
        lead(5, at(date(2026, 3, 31), 12)),
        lead(6, at(date(2026, 9, 6), 12), category="PARTNERSHIP", status_name="DESIGNER"),
        lead(7, at(date(2026, 7, 1), 12), category="WON", converted_at=at(date(2026, 8, 31), 19)),
    )

    trend = monthly_trend(leads, SEPTEMBER_END, app_config)

    assert trend.months[0] == date(2026, 4, 1)
    assert trend.months[-1] == date(2026, 9, 1)
    assert trend.leads == (2, 0, 0, 1, 0, 2)
    assert trend.contracts == (0, 0, 0, 0, 0, 3)
    assert trend.leads[-1] == monthly_funnel(leads, SEPTEMBER_END, app_config).company.leads


def test_monthly_loss_reasons_compare_with_previous_month(app_config: AppConfig) -> None:
    old = at(date(2026, 6, 1), 12)
    leads = frame(
        app_config,
        lost(1, old, "BUGET", status_changed_at=at(date(2026, 9, 3), 12)),
        lost(2, old, "BUGET", status_changed_at=at(date(2026, 9, 4), 12)),
        lost(3, at(date(2026, 9, 5), 12), "NU_RASPUNS"),
        lost(4, old, "BUGET", status_changed_at=at(date(2026, 8, 10), 12)),
        lost(5, old, "NU_RASPUNS", status_changed_at=at(date(2026, 8, 31), 19)),
        lost(6, old, "TIMP", status_changed_at=at(date(2026, 8, 31), 18)),
        lost(7, old, "TIMP", status_changed_at=at(SEPTEMBER_END, 19)),
    )

    losses = monthly_loss_reasons(leads, SEPTEMBER_END, app_config)

    assert (losses.month, losses.previous_month) == (date(2026, 9, 1), date(2026, 8, 1))
    assert losses.current.reason_total("BUGET") == 2
    assert losses.current.reason_total("NU_RASPUNS") == 2
    assert losses.current.reason_total("TIMP") == 0
    assert losses.previous.reason_total("BUGET") == 1
    assert losses.previous.reason_total("TIMP") == 1
    assert losses.previous.reason_total("NU_RASPUNS") == 0
    assert losses.reason_change("BUGET") == pytest.approx(1.0)
    assert losses.reason_change("TIMP") == pytest.approx(-1.0)
    assert losses.reason_change("NU_RASPUNS") is None
    assert losses.total_change == pytest.approx(1.0)
    assert losses.total_share == pytest.approx(1.0)


def test_monthly_loss_reason_share_and_order(app_config: AppConfig) -> None:
    old = at(date(2026, 6, 1), 12)
    leads = frame(
        app_config,
        lost(1, old, "NU_RASPUNS", status_changed_at=at(date(2026, 9, 3), 12)),
        lost(2, old, "BUGET", status_changed_at=at(date(2026, 9, 4), 12)),
        lost(3, old, "BUGET", status_changed_at=at(date(2026, 9, 5), 12)),
        lost(4, old, "NU_RASPUNS", status_changed_at=at(date(2026, 9, 6), 12)),
        lost(5, old, "BUGET", status_changed_at=at(date(2026, 8, 10), 12)),
        lost(6, old, "TIMP", status_changed_at=at(date(2026, 8, 11), 12)),
        lost(7, old, "TIMP", status_changed_at=at(date(2026, 8, 12), 12)),
    )

    losses = monthly_loss_reasons(leads, SEPTEMBER_END, app_config)

    assert losses.reasons_by_count == ("BUGET", "NU_RASPUNS", "TIMP")
    assert losses.reason_share("BUGET") == pytest.approx(0.5)
    assert losses.reason_share("NU_RASPUNS") == pytest.approx(0.5)
    assert losses.reason_share("TIMP") == pytest.approx(0.0)
    assert losses.total_share == pytest.approx(1.0)


def test_monthly_loss_shares_are_unknown_without_losses(app_config: AppConfig) -> None:
    leads = frame(
        app_config,
        lost(1, at(date(2026, 6, 1), 12), "BUGET", status_changed_at=at(date(2026, 8, 10), 12)),
    )

    losses = monthly_loss_reasons(leads, SEPTEMBER_END, app_config)

    assert losses.reasons_by_count == ("BUGET",)
    assert losses.reason_share("BUGET") is None
    assert losses.total_share is None
    assert losses.reason_change("BUGET") == pytest.approx(-1.0)


def test_monthly_lead_rows_are_company_leads_with_local_created_day(
    app_config: AppConfig,
) -> None:
    leads = frame(
        app_config,
        lead(1, at(date(2026, 8, 31), 21)),
        lead(2, at(date(2026, 9, 15), 9)),
        lead(3, at(date(2026, 9, 16), 9), category="PARTNERSHIP", status_name="DESIGNER"),
        lead(4, at(SEPTEMBER_END, 19)),
        lead(5, at(date(2026, 9, 20), 23, 30), source_name="Showroom"),
    )

    rows = monthly_lead_rows(leads, SEPTEMBER_END, app_config)

    assert tuple(rows.columns) == LEAD_ROW_COLUMNS
    assert list(rows["lead_id"]) == [1, 2, 5]
    assert list(rows["day"]) == [date(2026, 8, 31), date(2026, 9, 15), date(2026, 9, 20)]
    assert len(rows) == monthly_funnel(leads, SEPTEMBER_END, app_config).company.leads
