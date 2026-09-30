import re
import unicodedata
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from markupsafe import Markup

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"

# Названия дней недели в таблицах недельного отчёта как в ручном отчёте.
RO_WEEKDAYS = ("Luni", "Marți", "Miercuri", "Joi", "Vineri", "Sâmbătă", "Duminică")
RO_MONTHS = (
    "ianuarie",
    "februarie",
    "martie",
    "aprilie",
    "mai",
    "iunie",
    "iulie",
    "august",
    "septembrie",
    "octombrie",
    "noiembrie",
    "decembrie",
)


def week_range_label(days: tuple[date, ...]) -> str:
    first, last = days[0], days[-1]
    first_label = f"{first:%d}" if first.month == last.month else f"{first:%d.%m}"
    return f"{first_label}–{last:%d.%m}"


def day_ranges_label(days: tuple[date, ...]) -> str:
    runs: list[list[date]] = []
    for day in days:
        if runs and day - runs[-1][-1] == timedelta(days=1):
            runs[-1].append(day)
        else:
            runs.append([day])
    return ", ".join(
        f"{run[0]:%d.%m}" if len(run) == 1 else week_range_label(tuple(run)) for run in runs
    )


def dash_if_unknown(value: int | None) -> str:
    return "—" if value is None else str(value)


def count_noun(count: int, singular: str, plural: str) -> str:
    # Румынское числительное: 1 zi, 2–19 zile, «de» при нуле и от 20 по двум последним цифрам
    # (20 de zile, 100 de zile, но 101 zile, 119 zile).
    if count == 1:
        return f"1 {singular}"
    last_two_digits = count % 100
    if last_two_digits == 0 or last_two_digits >= 20:
        return f"{count} de {plural}"
    return f"{count} {plural}"


def percent(value: float | None) -> str:
    return "—" if value is None else f"{round(value * 100)}%"


def percent_one_decimal(value: float | None) -> str:
    # m4 и m5 рядом с порогами: целое округление показало бы 9,6 % как 10 %. Отметка цели
    # считается по точному значению, а не по подписи.
    return "—" if value is None else f"{value * 100:.1f}%".replace(".", ",")


def signed_percent_one_decimal(value: float) -> str:
    if value == 0:
        return percent_one_decimal(value)
    return ("+" if value > 0 else "−") + percent_one_decimal(abs(value))


def signed_count(value: int) -> str:
    if value == 0:
        return "0"
    return ("+" if value > 0 else "−") + str(abs(value))


def points_one_decimal(value: float) -> str:
    # Разница долей: «2,1 pp», а не «2,1%», чтобы её не читали как относительное изменение.
    return f"{abs(value) * 100:.1f} pp".replace(".", ",")


def signed_points_one_decimal(value: float) -> str:
    if round(value * 100, 1) == 0:
        return points_one_decimal(0)
    return ("+" if value > 0 else "−") + points_one_decimal(value)


LINE_BREAKING_CATEGORIES = frozenset({"Cc", "Zl", "Zp"})
PHONE_LIKE = re.compile(r"[0-9]{7,}")
PHONE_SEPARATORS = re.compile(r"[\s\-.()+]")


def campaign_label(value: str, max_length: int, hidden_label: str) -> str:
    # UTM_Campanie приходит из формы сайта как есть: перевод строки сломал бы таблицу и разбиение
    # сообщения, телефон или e-mail в нём это контакт клиента (инвариант 7). Данные не трогаем,
    # только подпись в отчёте.
    # Cf (zero-width, bidi) невидимы: U+202E развернул бы подпись, U+200B разорвал бы цифры
    # телефона мимо проверки. Удаляем их до проверки на контакт.
    flat = "".join(
        " " if unicodedata.category(character) in LINE_BREAKING_CATEGORIES else character
        for character in value
        if unicodedata.category(character) != "Cf"
    )
    if PHONE_LIKE.search(PHONE_SEPARATORS.sub("", flat)) or ("@" in flat and "." in flat):
        return hidden_label
    if len(flat) > max_length:
        return flat[: max_length - 1] + "…"
    return flat


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
    environment.filters["count_noun"] = count_noun
    return environment


def render(template_name: str, **values: Any) -> str:
    template = template_environment().get_template(f"{template_name}.j2")
    return template.render(**values).strip()


def text(macro_name: str, **values: Any) -> Markup:
    macro = getattr(template_environment().get_template("texts.j2").module, macro_name)
    return Markup(macro(**values))
