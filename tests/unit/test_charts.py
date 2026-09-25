from datetime import date

import pytest

from digest.metrics.kpi import LeadCounts
from digest.metrics.monthly import MonthlyFunnel, MonthlyTrend
from digest.reports.charts import ChartLabels, chart_labels, funnel_chart, trend_chart
from digest.reports.render import ReportLanguage

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def counts(leads: int, useful: int, offers: int, clienti: int) -> LeadCounts:
    return LeadCounts(
        leads=leads,
        irr_leads=leads - useful,
        useful=useful,
        clienti=clienti,
        offers=offers,
        nar=0,
        buget=0,
        pnp=0,
        showroom_visits=0,
        clienti_from_showroom=0,
        active_offers_14=0,
        unmapped=0,
    )


FUNNEL = MonthlyFunnel(
    date(2026, 9, 1),
    counts(227, 175, 82, 17),
    {
        "Brașov": counts(78, 61, 29, 6),
        "București": counts(94, 70, 33, 7),
        "Cluj": counts(55, 44, 20, 4),
        None: counts(0, 0, 0, 0),
    },
)
TREND = MonthlyTrend(
    tuple(date(2026, month, 1) for month in range(4, 10)),
    (180, 201, 195, 230, 210, 227),
    (11, 15, 16, 17, 19, 17),
)


@pytest.mark.parametrize("language", ["ro", "ru"])
def test_funnel_chart_returns_png_bytes(language: ReportLanguage) -> None:
    image = funnel_chart(FUNNEL, chart_labels(language))

    assert image.startswith(PNG_SIGNATURE)
    assert len(image) > len(PNG_SIGNATURE)


@pytest.mark.parametrize("language", ["ro", "ru"])
def test_trend_chart_returns_png_bytes(language: ReportLanguage) -> None:
    image = trend_chart(TREND, chart_labels(language))

    assert image.startswith(PNG_SIGNATURE)
    assert len(image) > len(PNG_SIGNATURE)


def test_charts_render_empty_month() -> None:
    empty = counts(0, 0, 0, 0)
    funnel = MonthlyFunnel(date(2026, 9, 1), empty, {"Brașov": empty, None: empty})
    trend = MonthlyTrend(TREND.months, (0,) * 6, (0,) * 6)

    assert funnel_chart(funnel, chart_labels("ro")).startswith(PNG_SIGNATURE)
    assert trend_chart(trend, chart_labels("ro")).startswith(PNG_SIGNATURE)


def test_chart_labels_have_same_keys_in_both_languages() -> None:
    romanian, russian = chart_labels("ro"), chart_labels("ru")

    assert set(romanian.model_dump()) == set(russian.model_dump()) == set(ChartLabels.model_fields)
    assert romanian.month_label(date(2026, 8, 1)) == "Aug 2026"
    assert russian.month_label(date(2026, 8, 1)) == "Авг 2026"
