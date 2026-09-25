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


def change_label(value: float | None) -> str:
    if value is None:
        return "(—)"
    if value == 0:
        return "(=)"
    sign = "+" if value > 0 else "–"
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
    environment.filters["change"] = change_label
    return environment


def render(template_name: str, language: ReportLanguage, **values: Any) -> str:
    template = template_environment().get_template(f"{template_name}.{language}.j2")
    return template.render(**values).strip()
