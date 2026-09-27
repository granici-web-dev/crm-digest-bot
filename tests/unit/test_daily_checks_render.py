from datetime import date

import pytest
from markupsafe import Markup
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
from digest.reports.lead_links import LeadLinks
from digest.reports.render import render

LINKS = LeadLinks("https://bellesofa.meficrm.com", "/admin/leads/index/{lead_id}", 10)
ROIBU_IDS = tuple(range(101, 113))
# Первая группа «не взяты» не самая старая: общий максимум печатается отдельно. У Roibu 12
# самых старых лидов: 10 ссылок и «și încă 2», у остальных групп ссылок нет (лимит на блок).
UNTOUCHED = UntouchedLeads(
    lead_count=14,
    oldest_age_hours=26,
    groups=(
        UntouchedGroup(None, 1, 5, (501,)),
        UntouchedGroup("Roibu Valeria", 12, 26, ROIBU_IDS),
        UntouchedGroup("Raileanu  Leon", 1, 1, (601,)),
    ),
    lead_ids=(*ROIBU_IDS, 501, 601),
)
# Лидов меньше лимита: ссылки у каждой группы.
OVERDUE = OverdueRevenire(
    lead_count=4,
    max_days_overdue=26,
    groups=(
        OverdueGroup(None, 1, 5, (501,)),
        OverdueGroup("Roibu Valeria", 2, 26, (101, 102)),
        OverdueGroup("Raileanu  Leon", 1, 1, (601,)),
    ),
    lead_ids=(101, 102, 501, 601),
)


def untouched_links(untouched: UntouchedLeads) -> list[Markup]:
    return LINKS.block_lines(untouched.lead_ids, [group.lead_ids for group in untouched.groups])


def overdue_links(overdue: OverdueRevenire) -> list[Markup]:
    return LINKS.block_lines(overdue.lead_ids, [group.lead_ids for group in overdue.groups])


@pytest.mark.parametrize(
    "untouched", [UNTOUCHED, UntouchedLeads(0, None, (), ())], ids=["found", "none"]
)
def test_untouched_leads_render(untouched: UntouchedLeads, snapshot: SnapshotAssertion) -> None:
    text = render("untouched_leads", untouched=untouched, links=untouched_links(untouched))

    assert text == snapshot


@pytest.mark.parametrize(
    "overdue", [OVERDUE, OverdueRevenire(0, None, (), ())], ids=["found", "none"]
)
def test_overdue_revenire_render(overdue: OverdueRevenire, snapshot: SnapshotAssertion) -> None:
    assert render("overdue_revenire", overdue=overdue, links=overdue_links(overdue)) == snapshot


@pytest.mark.parametrize(
    ("days", "expected"),
    [(1, "1 zi"), (2, "2 zile"), (19, "19 zile"), (20, "20 de zile"), (101, "101 zile")],
)
def test_overdue_revenire_ro_day_numerals(days: int, expected: str) -> None:
    overdue = OverdueRevenire(1, days, (OverdueGroup("Marc Andra", 1, days, (7,)),), (7,))

    text = render("overdue_revenire", overdue=overdue, links=overdue_links(overdue))

    assert f"(cea mai veche: {expected})" in text
    assert f"Marc Andra 1 ({expected}): " in text


def stale(
    counts: dict[str | None, int], change: int | None, ids: dict[str | None, tuple[int, ...]]
) -> StaleOffers:
    by_showroom_ids = {showroom: ids.get(showroom, ()) for showroom in counts}
    ordered = tuple(sorted(lead_id for group in by_showroom_ids.values() for lead_id in group))
    return StaleOffers(counts, sum(counts.values()), change, by_showroom_ids, ordered)


NO_OFFERS: dict[str | None, int] = {"Brașov": 0, "București": 0, "Cluj": 0, None: 0}


@pytest.mark.parametrize(
    "offers",
    [
        stale(
            {"Brașov": 5, "București": 0, "Cluj": 9, None: 1},
            2,
            {"Brașov": (1, 3, 5, 7, 9), "Cluj": (2, 4, 6, 8, 10, 11, 12, 13, 14), None: (15,)},
        ),
        stale({**NO_OFFERS, "Brașov": 1}, -3, {"Brașov": (1,)}),
        stale(
            {**NO_OFFERS, "Brașov": 2, "București": 1}, None, {"Brașov": (1, 2), "București": (3,)}
        ),
        stale(NO_OFFERS, None, {}),
        stale(NO_OFFERS, -2, {}),
    ],
    ids=["growth", "decline", "no_yesterday", "none", "none_after_decline"],
)
def test_stale_offers_render(offers: StaleOffers, snapshot: SnapshotAssertion) -> None:
    showrooms = list(offers.lead_ids_by_showroom)
    lines = LINKS.block_lines(
        offers.lead_ids, [offers.lead_ids_by_showroom[showroom] for showroom in showrooms]
    )
    links = dict(zip(showrooms, lines, strict=True))

    assert render("stale_offers", offers=offers, stale_days=14, links=links) == snapshot


@pytest.mark.parametrize(
    "found",
    [
        Anomalies(7, 2.2857, (IrelevantSpike("Dragoi Mihaela", 6), IrelevantSpike(None, 5))),
        Anomalies(7, 3.0, ()),
        Anomalies(7, None, (IrelevantSpike("Marc Andra", 5),)),
        Anomalies(7, None, ()),
    ],
    ids=["both", "site_only", "spike_only", "none"],
)
def test_anomalies_render(found: Anomalies, snapshot: SnapshotAssertion) -> None:
    assert render("anomalies", anomalies=found) == snapshot


@pytest.mark.parametrize(
    "week_ago_date", [date(2026, 9, 17), date(2026, 9, 20)], ids=["thursday", "sunday"]
)
def test_same_weekday_compare_render(week_ago_date: date, snapshot: SnapshotAssertion) -> None:
    comparison = SameWeekdayComparison(week_ago_date, 11, 8, 1, 0)

    assert render("same_weekday_compare", comparison=comparison) == snapshot
