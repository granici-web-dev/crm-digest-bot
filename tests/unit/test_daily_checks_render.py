from datetime import date

import pytest
from syrupy.assertion import SnapshotAssertion

from digest.metrics.daily_checks import (
    Anomalies,
    IrelevantSpike,
    OverdueGroup,
    OverdueRevenire,
    SameWeekdayComparison,
    StaleOffers,
    UntouchedGroup,
    UntouchedLeads,
)
from digest.reports.render import ReportLanguage, render

LANGUAGES = ["ro", "ru"]
# Первая группа «не взяты» не самая старая: общий максимум печатается отдельно.
UNTOUCHED = UntouchedLeads(
    lead_count=4,
    oldest_age_hours=26,
    groups=(
        UntouchedGroup(None, 1, 5),
        UntouchedGroup("Roibu Valeria", 2, 26),
        UntouchedGroup("Raileanu  Leon", 1, 1),
    ),
)
OVERDUE = OverdueRevenire(
    lead_count=4,
    max_days_overdue=26,
    groups=(
        OverdueGroup(None, 1, 5),
        OverdueGroup("Roibu Valeria", 2, 26),
        OverdueGroup("Raileanu  Leon", 1, 1),
    ),
)


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize(
    "untouched", [UNTOUCHED, UntouchedLeads(0, None, ())], ids=["found", "none"]
)
def test_untouched_leads_render(
    language: ReportLanguage, untouched: UntouchedLeads, snapshot: SnapshotAssertion
) -> None:
    assert render("untouched_leads", language, untouched=untouched) == snapshot


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize("overdue", [OVERDUE, OverdueRevenire(0, None, ())], ids=["found", "none"])
def test_overdue_revenire_render(
    language: ReportLanguage, overdue: OverdueRevenire, snapshot: SnapshotAssertion
) -> None:
    assert render("overdue_revenire", language, overdue=overdue) == snapshot


@pytest.mark.parametrize(
    ("days", "expected"),
    [(1, "1 zi"), (2, "2 zile"), (19, "19 zile"), (20, "20 de zile"), (101, "101 zile")],
)
def test_overdue_revenire_ro_day_numerals(days: int, expected: str) -> None:
    overdue = OverdueRevenire(1, days, (OverdueGroup("Marc Andra", 1, days),))

    text = render("overdue_revenire", "ro", overdue=overdue)

    assert f"(cea mai veche: {expected})" in text
    assert text.endswith(f"Marc Andra 1 ({expected})")


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize(
    "offers",
    [
        StaleOffers({"Brașov": 5, "București": 0, "Cluj": 9, None: 1}, 15, 2),
        StaleOffers({"Brașov": 1, "București": 0, "Cluj": 0, None: 0}, 1, -3),
        StaleOffers({"Brașov": 2, "București": 1, "Cluj": 0, None: 0}, 3, None),
        StaleOffers({"Brașov": 0, "București": 0, "Cluj": 0, None: 0}, 0, None),
        StaleOffers({"Brașov": 0, "București": 0, "Cluj": 0, None: 0}, 0, -2),
    ],
    ids=["growth", "decline", "no_yesterday", "none", "none_after_decline"],
)
def test_stale_offers_render(
    language: ReportLanguage, offers: StaleOffers, snapshot: SnapshotAssertion
) -> None:
    assert render("stale_offers", language, offers=offers, stale_days=14) == snapshot


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize(
    "found",
    [
        Anomalies(2.2857, (IrelevantSpike("Dragoi Mihaela", 6), IrelevantSpike(None, 5))),
        Anomalies(3.0, ()),
        Anomalies(None, (IrelevantSpike("Marc Andra", 5),)),
        Anomalies(None, ()),
    ],
    ids=["both", "site_only", "spike_only", "none"],
)
def test_anomalies_render(
    language: ReportLanguage, found: Anomalies, snapshot: SnapshotAssertion
) -> None:
    assert render("anomalies", language, anomalies=found) == snapshot


@pytest.mark.parametrize("language", LANGUAGES)
@pytest.mark.parametrize(
    "week_ago_date", [date(2026, 9, 17), date(2026, 9, 20)], ids=["thursday", "sunday"]
)
def test_same_weekday_compare_render(
    language: ReportLanguage, week_ago_date: date, snapshot: SnapshotAssertion
) -> None:
    comparison = SameWeekdayComparison(week_ago_date, 11, 8, 1, 0)

    assert render("same_weekday_compare", language, comparison=comparison) == snapshot
