from functools import cache
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"

ReportLanguage = Literal["ro", "ru"]
# Названия дней недели в таблицах недельного отчёта как в ручном отчёте, в обоих языках.
RO_WEEKDAYS = ("Luni", "Marți", "Miercuri", "Joi", "Vineri", "Sâmbătă", "Duminică")


def dash_if_unknown(value: int | None) -> str:
    return "—" if value is None else str(value)


def percent(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)}%"


def percent_one_decimal(value: float | None) -> str:
    # m4 и m5 рядом с порогами: целое округление показало бы 9,6 % как 10 %. Отметка цели
    # считается по точному значению, а не по подписи.
    return "—" if value is None else f"{value * 100:.1f}%".replace(".", ",")


TARGET_DIRECTION_SIGNS = {"higher": "≥", "lower": "≤"}


def target_label(value: float, direction: str) -> str:
    # Цель в том же формате, что факт рядом с ней (percent1).
    return f"{TARGET_DIRECTION_SIGNS[direction]}{percent_one_decimal(value)}"


def change_label(value: float | None) -> str:
    if value is None:
        return "(—)"
    if value == 0:
        return "(=)"
    sign = "+" if value > 0 else "−"
    return f"({sign}{round(abs(value) * 100)}%)"


@cache
def template_environment() -> Environment:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["dash"] = dash_if_unknown
    environment.filters["percent"] = percent
    environment.filters["percent1"] = percent_one_decimal
    environment.filters["change"] = change_label
    return environment


def render(template_name: str, language: ReportLanguage, **values: Any) -> str:
    template = template_environment().get_template(f"{template_name}.{language}.j2")
    return template.render(**values).strip()
