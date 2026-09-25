from datetime import date
from functools import cache
from io import BytesIO
from math import ceil
from typing import Annotated

import yaml
from matplotlib.axes import Axes
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from pydantic import Field

from digest.config import StrictConfigModel
from digest.metrics.kpi import LeadCounts
from digest.metrics.monthly import MonthlyFunnel, MonthlyTrend
from digest.reports.render import TEMPLATES_DIR, ReportLanguage

SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
BASELINE = "#d9d8d4"
LEADS_COLOR = "#2a78d6"
CONTRACTS_COLOR = "#eb6834"
DPI = 150
FUNNEL_PANEL_COLUMNS = 2


class ChartLabels(StrictConfigModel):
    months: Annotated[list[str], Field(min_length=12, max_length=12)]
    funnel_title: str
    funnel_stages: Annotated[list[str], Field(min_length=4, max_length=4)]
    clienti_note: str
    company: str
    without_showroom_leads: str
    trend_title: str
    trend_leads: str
    trend_contracts: str
    trend_note: str

    def month_name(self, month: date) -> str:
        return self.months[month.month - 1]

    def month_label(self, month: date) -> str:
        return f"{self.month_name(month)} {month:%Y}"

    def without_showroom_note(self, counts: LeadCounts) -> str | None:
        if not counts.leads:
            return None
        return self.without_showroom_leads.format(leads=counts.leads, irrelevant=counts.irr_leads)


@cache
def chart_labels(language: ReportLanguage) -> ChartLabels:
    with (TEMPLATES_DIR / f"charts.{language}.yaml").open(encoding="utf-8") as file:
        return ChartLabels.model_validate(yaml.safe_load(file))


def new_figure(width: float, height: float) -> Figure:
    # Figure без pyplot: нет глобального реестра фигур, долгоживущий процесс не копит память.
    figure = Figure(figsize=(width, height), dpi=DPI, facecolor=SURFACE, layout="constrained")
    FigureCanvasAgg(figure)
    return figure


def png_bytes(figure: Figure) -> bytes:
    output = BytesIO()
    figure.savefig(output, format="png", facecolor=SURFACE)
    return output.getvalue()


def style_axes(axes: Axes) -> None:
    axes.set_facecolor(SURFACE)
    for side in ("top", "right", "left", "bottom"):
        axes.spines[side].set_visible(False)
    axes.tick_params(colors=TEXT_SECONDARY, length=0, labelsize=9)


def draw_funnel_panel(axes: Axes, title: str, counts: LeadCounts, labels: ChartLabels) -> None:
    values = [counts.leads, counts.useful, counts.offers, counts.clienti]
    positions = range(len(values))
    axes.barh(positions, values, height=0.6, color=LEADS_COLOR)
    axes.invert_yaxis()
    axes.set_yticks(list(positions), labels.funnel_stages)
    axes.set_xticks([])
    axes.set_xlim(0, max(values) * 1.2 or 1)
    axes.axvline(0, color=BASELINE, linewidth=1)
    for position, value in zip(positions, values, strict=True):
        axes.annotate(
            str(value),
            (value, position),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            color=TEXT_PRIMARY,
            fontsize=9,
        )
    axes.set_title(title, loc="left", color=TEXT_PRIMARY, fontsize=11)
    style_axes(axes)


def funnel_chart(funnel: MonthlyFunnel, labels: ChartLabels) -> bytes:
    # Лиды без шоурума без своей панели: у них нет воронки, только строка в сноске.
    panels = [
        (showroom, funnel.by_showroom[showroom]) for showroom in funnel.named_showrooms_with_leads
    ]
    panels.append((labels.company, funnel.company))
    rows = ceil(len(panels) / FUNNEL_PANEL_COLUMNS)
    figure = new_figure(10, 0.6 + 2.2 * rows)
    figure.suptitle(
        f"{labels.funnel_title} · {labels.month_label(funnel.month)}",
        color=TEXT_PRIMARY,
        fontsize=13,
        x=0.01,
        ha="left",
    )
    grid = figure.subplots(rows, FUNNEL_PANEL_COLUMNS, squeeze=False)
    cells = [axes for row in grid for axes in row]
    for axes, (title, counts) in zip(cells, panels, strict=False):
        draw_funnel_panel(axes, title, counts, labels)
    for axes in cells[len(panels) :]:
        axes.set_visible(False)
    notes = [labels.clienti_note, labels.without_showroom_note(funnel.without_showroom)]
    figure.supxlabel(
        "\n".join(note for note in notes if note),
        color=TEXT_SECONDARY,
        fontsize=8,
        x=0.01,
        ha="left",
    )
    return png_bytes(figure)


def draw_trend_panel(
    axes: Axes, title: str, values: tuple[int, ...], color: str, month_labels: list[str]
) -> None:
    positions = range(len(values))
    axes.bar(positions, values, width=0.5, color=color)
    axes.set_xticks(list(positions), month_labels)
    axes.set_yticks([])
    axes.set_ylim(0, max(values) * 1.25 or 1)
    axes.axhline(0, color=BASELINE, linewidth=1)
    for position, value in zip(positions, values, strict=True):
        axes.annotate(
            str(value),
            (position, value),
            xytext=(0, 3),
            textcoords="offset points",
            ha="center",
            color=TEXT_PRIMARY,
            fontsize=9,
        )
    axes.set_title(title, loc="left", color=TEXT_PRIMARY, fontsize=11)
    style_axes(axes)


def trend_chart(trend: MonthlyTrend, labels: ChartLabels) -> bytes:
    # Лиды и контракты на двух панелях, не на двух осях Y одной: шкалы отличаются на порядок.
    month_labels = [labels.month_label(month) for month in trend.months]
    figure = new_figure(10, 6)
    figure.suptitle(labels.trend_title, color=TEXT_PRIMARY, fontsize=13, x=0.01, ha="left")
    leads_axes, contracts_axes = figure.subplots(2, 1)
    draw_trend_panel(leads_axes, labels.trend_leads, trend.leads, LEADS_COLOR, month_labels)
    draw_trend_panel(
        contracts_axes, labels.trend_contracts, trend.contracts, CONTRACTS_COLOR, month_labels
    )
    figure.supxlabel(labels.trend_note, color=TEXT_SECONDARY, fontsize=8, x=0.01, ha="left")
    return png_bytes(figure)
