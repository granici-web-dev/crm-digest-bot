from functools import cache
from pathlib import Path
from typing import Any, Literal

from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"

ReportLanguage = Literal["ro", "ru"]


def dash_if_unknown(value: int | None) -> str:
    return "—" if value is None else str(value)


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
    return environment


def render(template_name: str, language: ReportLanguage, **values: Any) -> str:
    template = template_environment().get_template(f"{template_name}.{language}.j2")
    return template.render(**values).strip()
