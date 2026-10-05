from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from digest.config import AppConfig
from digest.metrics.daily import daily_window
from digest.metrics.extra import cohort_conversion
from digest.metrics.frame import prepare_lead_frame
from digest.metrics.kpi import Period
from digest.metrics.monthly import (
    BELOW_ALL_LEVELS,
    REPEAT_BY_CONTACT,
    REPEAT_BY_SOURCE,
    RepeatClientCounts,
    ScrRow,
    deal_cycle,
    month_window,
    monthly_client_rows,
    monthly_cohort_conversion,
    monthly_funnel,
    monthly_lead_rows,
    monthly_loss_reasons,
    monthly_repeat_clients,
    monthly_scr,
    monthly_source_conversion,
    monthly_trend,
    scr_level,
    trend_months,
)
from digest.metrics.weekly import LEAD_ROW_COLUMNS
from factories import BUCHAREST, TEST_ACCOUNT, config_with_test_account, make_snapshot_row

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


def test_monthly_lead_rows_are_company_leads_with_window_day(
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
    # День ежедневного окна, как d1 и m3: после 19:00 это следующий день.
    assert list(rows["day"]) == [date(2026, 9, 1), date(2026, 9, 15), date(2026, 9, 21)]
    assert len(rows) == monthly_funnel(leads, SEPTEMBER_END, app_config).company.leads


def client(
    lead_id: int, converted_at: datetime | None, phone: str | None, **overrides: Any
) -> dict[str, Any]:
    return lead(
        lead_id,
        overrides.pop("created_at", at(date(2026, 1, 5), 12)),
        category="WON",
        status_name="Clienți",
        converted_at=converted_at,
        contact_phone_key=phone,
        **overrides,
    )


def test_repeat_client_matches_earlier_client_by_phone_or_email(app_config: AppConfig) -> None:
    leads = frame(
        app_config,
        client(1, at(date(2026, 3, 10), 12), "phone-a"),
        client(2, at(date(2026, 4, 10), 12), None, contact_email_key="email-b"),
        client(3, at(date(2026, 9, 10), 12), "phone-a"),
        client(4, at(date(2026, 9, 11), 12), "phone-other", contact_email_key="email-b"),
        client(5, at(date(2026, 9, 12), 12), "phone-new"),
    )

    repeat_clients = monthly_repeat_clients(leads, SEPTEMBER_END, app_config)

    assert repeat_clients.company == RepeatClientCounts(clients=3, repeat=2)
    assert repeat_clients.company.share == pytest.approx(2 / 3)


def test_first_purchase_is_not_repeat_even_if_client_bought_again_later(
    app_config: AppConfig,
) -> None:
    leads = frame(
        app_config,
        client(1, at(date(2026, 9, 10), 12), "phone-a"),
        client(2, at(date(2026, 10, 10), 12), "phone-a"),
    )

    assert monthly_repeat_clients(leads, SEPTEMBER_END, app_config).company == (
        RepeatClientCounts(clients=1, repeat=0)
    )


def test_match_with_lead_that_is_not_won_does_not_count(app_config: AppConfig) -> None:
    leads = frame(
        app_config,
        lead(1, at(date(2026, 3, 10), 12), contact_phone_key="phone-a"),
        lost(2, at(date(2026, 4, 10), 12), "BUGET", contact_phone_key="phone-a"),
        # converted_at без категории WON: статус сменили после конверсии, это не клиент.
        lead(
            3,
            at(date(2026, 5, 10), 12),
            converted_at=at(date(2026, 5, 11), 12),
            contact_phone_key="phone-a",
        ),
        client(4, at(date(2026, 9, 10), 12), "phone-a"),
    )

    assert monthly_repeat_clients(leads, SEPTEMBER_END, app_config).company == (
        RepeatClientCounts(clients=1, repeat=0)
    )


def test_clients_converted_at_same_moment_do_not_repeat_each_other(
    app_config: AppConfig,
) -> None:
    same_moment = at(date(2026, 9, 10), 12)
    leads = frame(
        app_config,
        client(1, same_moment, "phone-a"),
        client(2, same_moment, "phone-a"),
        client(3, at(date(2026, 9, 10), 12, 0, 1), "phone-a"),
    )

    assert monthly_repeat_clients(leads, SEPTEMBER_END, app_config).company == (
        RepeatClientCounts(clients=3, repeat=1)
    )


def test_repeat_clients_by_showroom_of_month_lead(app_config: AppConfig) -> None:
    leads = frame(
        app_config,
        client(1, at(date(2026, 3, 10), 12), "phone-a", showroom="Cluj"),
        client(2, at(date(2026, 9, 10), 12), "phone-a", showroom="București"),
        client(3, at(date(2026, 9, 11), 12), "phone-b", showroom="Cluj"),
        client(4, at(date(2026, 9, 12), 12), "phone-c", showroom=None),
    )

    by_showroom = monthly_repeat_clients(leads, SEPTEMBER_END, app_config).by_showroom

    assert by_showroom == {
        "Brașov": RepeatClientCounts(clients=0, repeat=0),
        "București": RepeatClientCounts(clients=1, repeat=1),
        "Cluj": RepeatClientCounts(clients=1, repeat=0),
        None: RepeatClientCounts(clients=1, repeat=0),
    }
    assert by_showroom["Brașov"].share is None


def test_won_without_converted_at_is_counted_apart_and_never_matched(
    app_config: AppConfig,
) -> None:
    leads = frame(
        app_config,
        client(1, None, "phone-a"),
        client(2, None, "phone-b"),
        client(3, at(date(2026, 9, 10), 12), "phone-a"),
    )

    repeat_clients = monthly_repeat_clients(leads, SEPTEMBER_END, app_config)

    assert repeat_clients.company == RepeatClientCounts(clients=1, repeat=0)
    assert repeat_clients.won_without_converted_at == 2


def test_month_clients_follow_month_window_by_converted_at(app_config: AppConfig) -> None:
    leads = frame(
        app_config,
        client(1, at(date(2026, 8, 31), 19), "phone-a"),
        client(2, at(SEPTEMBER_END, 18, 59), "phone-b"),
        client(3, at(SEPTEMBER_END, 19), "phone-c"),
    )

    repeat_clients = monthly_repeat_clients(leads, SEPTEMBER_END, app_config)

    assert repeat_clients.company == RepeatClientCounts(clients=2, repeat=0)
    assert repeat_clients.company.share == 0


def test_monthly_client_rows_are_month_clients_with_repeat_mark(app_config: AppConfig) -> None:
    leads = frame(
        app_config,
        client(1, at(date(2026, 3, 10), 12), "phone-a"),
        client(2, at(date(2026, 9, 20), 12), "phone-a", created_at=at(date(2026, 7, 1), 12)),
        client(3, at(date(2026, 9, 2), 22), "phone-b"),
    )

    rows = monthly_client_rows(leads, SEPTEMBER_END, app_config)

    assert tuple(rows.columns) == (*LEAD_ROW_COLUMNS, "is_repeat", "repeat_reason")
    assert list(rows["lead_id"]) == [3, 2]
    # Договор 02.09 22:00 это окно 03.09, как в d6 и m3.
    assert list(rows["day"]) == [date(2026, 9, 3), date(2026, 9, 20)]
    assert list(rows["is_repeat"]) == [False, True]
    assert list(rows["repeat_reason"]) == [None, REPEAT_BY_CONTACT]
    assert len(rows) == monthly_repeat_clients(leads, SEPTEMBER_END, app_config).company.clients


def test_repeat_client_source_counts_once_and_contact_match_wins(app_config: AppConfig) -> None:
    leads = frame(
        app_config,
        client(1, at(date(2026, 3, 10), 12), "phone-a"),
        # Первая покупка до mefi: контакт не совпадает, повторный по источнику.
        client(2, at(date(2026, 9, 10), 12), "phone-new", source_name="Client Fidel"),
        # Оба признака: одна причина, контакт, считается один раз.
        client(3, at(date(2026, 9, 11), 12), "phone-a", source_name="Client Fidel"),
        client(4, at(date(2026, 9, 12), 12), "phone-b", source_name="Telefon"),
        # Client Fidel вне месяца в клиенты месяца не входит.
        client(5, at(date(2026, 8, 10), 12), "phone-c", source_name="Client Fidel"),
    )

    repeat_clients = monthly_repeat_clients(leads, SEPTEMBER_END, app_config)
    rows = monthly_client_rows(leads, SEPTEMBER_END, app_config)

    assert repeat_clients.company == RepeatClientCounts(clients=3, repeat=2, by_source=1)
    assert repeat_clients.company.by_contact == 1
    assert dict(zip(rows["lead_id"], rows["repeat_reason"], strict=True)) == {
        2: REPEAT_BY_SOURCE,
        3: REPEAT_BY_CONTACT,
        4: None,
    }


def test_repeat_client_source_without_converted_at_is_not_a_month_client(
    app_config: AppConfig,
) -> None:
    leads = frame(app_config, client(1, None, "phone-a", source_name="Client Fidel"))

    repeat_clients = monthly_repeat_clients(leads, SEPTEMBER_END, app_config)

    assert repeat_clients.company == RepeatClientCounts(clients=0, repeat=0)
    assert repeat_clients.won_without_converted_at == 1


def test_campaign_share_counts_only_leads_with_campaign(app_config: AppConfig) -> None:
    in_september = at(date(2026, 9, 10), 12)
    lead_frame = frame(
        app_config,
        lead(1, in_september, utm_campanie="BZA_Cluj_Website_Leads 03"),
        lead(2, in_september, utm_campanie="BZA_Cluj_Website_Leads 03"),
        lead(3, in_september, source_name="Showroom"),
        lead(4, in_september, source_name=None),
        lead(5, at(date(2026, 8, 10), 12), utm_campanie="BZA_Cluj_Website_Leads 03"),
    )

    conversion = monthly_source_conversion(lead_frame, SEPTEMBER_END, app_config)

    assert (conversion.leads_with_campaign, conversion.leads_total) == (2, 4)
    assert conversion.campaign_share == 0.5
    assert (
        conversion.by_source.total.counts
        == monthly_funnel(lead_frame, SEPTEMBER_END, app_config).company
    )
    assert conversion.by_source.without_key is not None
    assert conversion.by_source.without_key.counts.leads == 1


def test_month_without_campaigns_has_zero_share(app_config: AppConfig) -> None:
    lead_frame = frame(app_config, lead(1, at(date(2026, 9, 10), 12)))

    conversion = monthly_source_conversion(lead_frame, SEPTEMBER_END, app_config)

    assert conversion.leads_with_campaign == 0
    assert conversion.campaign_share == 0
    assert conversion.by_campaign.rows == ()
    assert conversion.by_campaign.other is None


def cohort_client(
    lead_id: int, created_at: datetime, converted_at: datetime | None, **overrides: Any
) -> dict[str, Any]:
    return lead(
        lead_id,
        created_at,
        **{"category": "WON", "status_name": "Clienți", "converted_at": converted_at, **overrides},
    )


def irelevant(lead_id: int, created_at: datetime, **overrides: Any) -> dict[str, Any]:
    return lost(lead_id, created_at, "IRELEVANT", status_name="IRELEVANT", **overrides)


SEPTEMBER, AUGUST, JULY, JUNE = (date(2026, month, 10) for month in (9, 8, 7, 6))


def test_cohort_rate_equals_scr_of_monthly_funnel(app_config: AppConfig) -> None:
    leads = frame(
        app_config,
        lead(1, at(SEPTEMBER, 12)),
        lead(2, at(SEPTEMBER, 12)),
        irelevant(3, at(SEPTEMBER, 12)),
        cohort_client(4, at(SEPTEMBER, 12), at(date(2026, 9, 12), 12)),
        cohort_client(5, at(SEPTEMBER, 13), None),
    )

    cohorts = monthly_cohort_conversion(leads, SEPTEMBER_END, app_config).cohorts
    september = cohorts[-1]

    assert [row.month for row in cohorts] == list(trend_months(SEPTEMBER_END))
    assert september.conversion == pytest.approx(2 / 4)
    assert september.conversion == (
        monthly_scr(monthly_funnel(leads, SEPTEMBER_END, app_config), app_config).company.scr
    )
    assert september.conversion == cohort_conversion(
        leads, month_window(SEPTEMBER_END, app_config.status_mapping.time), app_config
    )


def test_cohort_excludes_partnership_and_test_accounts(app_config: AppConfig) -> None:
    converted_at = at(date(2026, 6, 12), 12)
    config = config_with_test_account(app_config)
    leads = frame(
        config,
        cohort_client(1, at(JUNE, 12), converted_at),
        cohort_client(
            2, at(JUNE, 12), converted_at, category="PARTNERSHIP", status_name="DESIGNER"
        ),
        cohort_client(3, at(JUNE, 12), converted_at, assigned_to_id=TEST_ACCOUNT.id),
    )

    june = monthly_cohort_conversion(leads, SEPTEMBER_END, config).cohorts[2]

    assert (june.counts.leads, june.counts.clienti, june.clients_with_date) == (1, 1, 1)


def test_lead_created_after_19_on_last_day_belongs_to_next_cohort(app_config: AppConfig) -> None:
    converted_at = at(date(2026, 9, 5), 12)
    leads = frame(
        app_config,
        cohort_client(1, at(date(2026, 8, 31), 18, 59, 59), converted_at),
        cohort_client(2, at(date(2026, 8, 31), 19), converted_at),
    )

    cohorts = monthly_cohort_conversion(leads, SEPTEMBER_END, app_config).cohorts

    assert (cohorts[-2].counts.clienti, cohorts[-1].counts.clienti) == (1, 1)
    assert cohorts[-2].median_days == pytest.approx(4 + 17 / 24 + 1 / 86_400)
    assert cohorts[-1].median_days == pytest.approx(4 + 17 / 24)


def test_conversion_after_exactly_30_days_is_in_30_day_bucket(app_config: AppConfig) -> None:
    created_at = at(JUNE, 12)
    leads = frame(app_config, cohort_client(1, created_at, at(date(2026, 7, 10), 12)))

    june = monthly_cohort_conversion(leads, SEPTEMBER_END, app_config).cohorts[2]

    assert june.clients_within_days == {7: 0, 30: 1, 90: 1}
    assert june.median_days == 30


def test_conversion_after_30_days_and_one_second_is_in_90_day_bucket(
    app_config: AppConfig,
) -> None:
    leads = frame(app_config, cohort_client(1, at(JUNE, 12), at(date(2026, 7, 10), 12, 0, 1)))

    june = monthly_cohort_conversion(leads, SEPTEMBER_END, app_config).cohorts[2]

    assert june.clients_within_days == {7: 0, 30: 0, 90: 1}


def test_bucket_is_unknown_until_cohort_is_old_enough(app_config: AppConfig) -> None:
    leads = frame(
        app_config,
        *(
            cohort_client(month.month, at(month, 12), at(month, 13))
            for month in (JUNE, JULY, AUGUST)
        ),
        cohort_client(9, at(SEPTEMBER, 12), at(SEPTEMBER, 13)),
    )

    june, july, august, september = monthly_cohort_conversion(
        leads, SEPTEMBER_END, app_config
    ).cohorts[2:]

    assert (june.age_days, july.age_days, august.age_days, september.age_days) == (92, 61, 30, 0)
    assert september.clients_within_days == {7: None, 30: None, 90: None}
    assert august.clients_within_days == {7: 1, 30: 1, 90: None}
    assert july.clients_within_days == {7: 1, 30: 1, 90: None}
    assert june.clients_within_days == {7: 1, 30: 1, 90: 1}
    assert (june.in_progress, july.in_progress, august.in_progress) == (False, True, True)
    assert september.in_progress
    assert august.share_within(30) == pytest.approx(1.0)
    assert august.share_within(90) is None
    assert september.median_days == pytest.approx(1 / 24)


def test_client_without_converted_at_counts_in_rate_not_in_days(app_config: AppConfig) -> None:
    leads = frame(
        app_config,
        cohort_client(1, at(JUNE, 12), at(date(2026, 6, 13), 12)),
        cohort_client(2, at(JUNE, 12), None),
        lead(3, at(JUNE, 12)),
        lead(4, at(JUNE, 12)),
    )

    result = monthly_cohort_conversion(leads, SEPTEMBER_END, app_config)
    june = result.cohorts[2]

    assert june.conversion == pytest.approx(2 / 4)
    assert (june.counts.clienti, june.clients_with_date) == (2, 1)
    assert june.clients_within_days == {7: 1, 30: 1, 90: 1}
    assert june.share_within(90) == pytest.approx(1 / 4)
    assert june.median_days == 3
    assert result.clients_without_converted_at == 1


def test_converted_lead_no_longer_clienti_is_in_cycle_not_in_cohort(app_config: AppConfig) -> None:
    leads = frame(
        app_config,
        cohort_client(1, at(SEPTEMBER, 12), at(date(2026, 9, 11), 12)),
        lost(
            2,
            at(SEPTEMBER, 12),
            "BUGET",
            status_name="BUGET",
            converted_at=at(date(2026, 9, 13), 12),
        ),
        cohort_client(3, at(JULY, 12), at(date(2026, 9, 20), 12)),
        cohort_client(4, at(JULY, 12), at(date(2026, 8, 20), 12)),
    )

    result = monthly_cohort_conversion(leads, SEPTEMBER_END, app_config)

    assert (result.cohorts[-1].counts.clienti, result.cohorts[-1].clients_with_date) == (1, 1)
    assert result.cycle.contracts == 3
    assert result.cycle.contracts == monthly_trend(leads, SEPTEMBER_END, app_config).contracts[-1]


def test_deal_cycle_median_p75_and_fast_share(app_config: AppConfig) -> None:
    created_at = at(date(2026, 9, 1), 12)
    converted = frame(
        app_config,
        *(
            cohort_client(days, created_at, at(date(2026, 9, 1 + days), 12))
            for days in (1, 2, 3, 10)
        ),
    )

    cycle = deal_cycle(converted, fast_cycle_days=7)

    assert cycle.contracts == 4
    assert cycle.median_days == pytest.approx(2.5)
    assert cycle.p75_days == pytest.approx(4.75)
    assert cycle.within_fast_days == 3
    assert cycle.share_within_fast == pytest.approx(3 / 4)


def test_converted_before_created_counts_as_zero_days(app_config: AppConfig) -> None:
    leads = frame(app_config, cohort_client(1, at(SEPTEMBER, 12), at(date(2026, 9, 8), 12)))

    result = monthly_cohort_conversion(leads, SEPTEMBER_END, app_config)

    assert result.cohorts[-1].median_days == 0
    assert result.cycle.median_days == 0
    assert result.cycle.within_fast_days == 1


def test_empty_cohort_and_month_without_contracts_give_none(app_config: AppConfig) -> None:
    leads = frame(app_config, lead(1, at(SEPTEMBER, 12)))

    result = monthly_cohort_conversion(leads, SEPTEMBER_END, app_config)
    june = result.cohorts[2]

    assert june.counts.leads == 0
    assert june.conversion is None
    assert june.share_within(30) is None
    assert june.median_days is None
    assert result.cohorts[-1].median_days is None
    assert result.cycle == type(result.cycle)(0, None, None, 0)
    assert result.cycle.share_within_fast is None


def test_days_across_dst_change_are_elapsed_time(app_config: AppConfig) -> None:
    leads = frame(
        app_config, cohort_client(1, at(date(2026, 10, 24), 12), at(date(2026, 10, 26), 12))
    )

    october = monthly_cohort_conversion(leads, date(2026, 10, 31), app_config).cohorts[-1]

    assert october.median_days == pytest.approx(2 + 1 / 24)


def test_showroom_cohorts_sum_to_company(app_config: AppConfig) -> None:
    leads = frame(
        app_config,
        cohort_client(1, at(JULY, 12), at(date(2026, 7, 12), 12), showroom="Brașov"),
        cohort_client(2, at(JULY, 12), at(date(2026, 9, 12), 12), showroom="Cluj"),
        lead(3, at(JULY, 12), showroom="Cluj"),
        irelevant(4, at(JULY, 12), showroom=None),
        cohort_client(5, at(SEPTEMBER, 12), at(date(2026, 9, 11), 12), showroom="Iași"),
    )

    result = monthly_cohort_conversion(leads, SEPTEMBER_END, app_config)

    assert list(result.cohorts_by_showroom) == ["Brașov", "București", "Cluj", "Iași", None]
    for index, company in enumerate(result.cohorts):
        rows = [cohorts[index] for cohorts in result.cohorts_by_showroom.values()]
        assert sum(row.counts.leads for row in rows) == company.counts.leads
        assert sum(row.counts.clienti for row in rows) == company.counts.clienti
        assert sum(row.clients_with_date for row in rows) == company.clients_with_date
    assert sum(cycle.contracts for cycle in result.cycle_by_showroom.values()) == 2
    assert result.cycle_by_showroom["Cluj"].median_days == pytest.approx(64)
    assert result.cohorts_by_showroom["Cluj"][-3].conversion == pytest.approx(1 / 2)


def test_cohort_age_is_elapsed_time_across_dst_change(app_config: AppConfig) -> None:
    # 31.12 19:00 EET → 31.03 19:00 EEST: 90 суток по часам, 90 суток без часа прошедшим временем.
    december = date(2026, 12, 10)
    leads = frame(app_config, cohort_client(1, at(december, 12), at(december, 13)))

    cohorts = monthly_cohort_conversion(leads, date(2027, 3, 31), app_config).cohorts
    december_cohort = cohorts[-4]

    assert december_cohort.month == date(2026, 12, 1)
    assert december_cohort.age_days == pytest.approx(90 - 1 / 24)
    assert december_cohort.clients_within_days == {7: 1, 30: 1, 90: None}
    assert december_cohort.in_progress
