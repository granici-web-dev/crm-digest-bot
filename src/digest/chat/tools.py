from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Annotated, Any

import pandas as pd
from anthropic.types import ToolParam
from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    Tag,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

from digest.config import KPI_NAMES, AppConfig, ChatToolName
from digest.db.lead_frame import SnapshotMissingError
from digest.metrics.breakdown import (
    BREAKDOWN_COLUMNS,
    BreakdownColumn,
    LeadBreakdown,
    lead_breakdown,
)
from digest.metrics.chat_periods import (
    CHAT_PERIODS,
    ChatDay,
    ChatMonth,
    ChatPeriod,
    ChatPeriodChoice,
    chat_period_window,
    earliest_specific_day,
    period_days,
    period_snapshot_date,
)
from digest.metrics.cockpit import manager_cockpit_table
from digest.metrics.daily import PreviousSnapshot
from digest.metrics.daily_checks import (
    MissingFollowupDate,
    missing_followup_date,
    overdue_revenire_by_manager,
    untouched_leads,
)
from digest.metrics.extra import period_delta, value_difference
from digest.metrics.kpi import (
    COUNT_NAMES,
    LeadCounts,
    Period,
    add_counts,
    kpis_from,
    lead_counts,
    lead_counts_by_showroom,
)
from digest.metrics.monthly import RepeatClientCounts, monthly_repeat_clients
from digest.metrics.touches import ManagerTouches, manager_touches, touch_snapshot_dates
from digest.metrics.weekly import (
    LossReasons,
    converted_count,
    converted_count_by_showroom,
    loss_reasons_in_window,
)
from digest.reports.lead_links import LeadLinks
from digest.reports.render import (
    RO_MONTHS,
    campaign_label,
    day_ranges_label,
    percent_one_decimal,
    points_one_decimal,
    signed_count,
    signed_percent_one_decimal,
    signed_points_one_decimal,
    target_label,
    text,
)

ALL_SHOWROOMS = "toate"
ALL_MANAGERS = "toti"
CONTRACTS_METRIC = "contracts"
COMPARABLE_METRICS: tuple[str, ...] = (*COUNT_NAMES, *KPI_NAMES, CONTRACTS_METRIC)
MONTH_PERIODS: tuple[ChatPeriod, ...] = ("luna_curenta", "luna_trecuta")
# Касания только по дням и неделям: месяц это до 31 загрузки снапшота на вопрос, и отчёт-двойник
# w14 недельный (shape chat-new-tools).
TOUCH_PERIODS: tuple[ChatPeriod, ...] = ("azi", "ieri", "saptamana_curenta", "saptamana_trecuta")

FrameLoader = Callable[[date], Awaitable[pd.DataFrame]]


@dataclass(frozen=True)
class ToolData:
    today: date
    # Даты успешных снапшотов по возрастанию.
    snapshot_dates: tuple[date, ...]
    # Бросает SnapshotMissingError, если за дату нет успешного снапшота.
    load_frame: FrameLoader
    config: AppConfig
    lead_links: LeadLinks


@dataclass(frozen=True)
class ToolOutcome:
    content: dict[str, Any]
    is_error: bool
    scope: str | None = None
    snapshot_dates: tuple[date, ...] = ()
    snapshot_notes: tuple[str, ...] = ()
    # Имена из данных с цифрами (подписи кампаний, источники вне конфига): страж маскирует их, как
    # имена mefi из конфига, иначе «Promo 30» в результате разрешила бы голую «30».
    masked_names: tuple[str, ...] = ()
    # id лидов для строки ссылок под ответом, самые старые первыми. Модели не передаются
    # (инвариант 7): в content их нет.
    lead_ids: tuple[int, ...] = ()
    # «Нет снапшота за дату» это ответ инструмента, а не сбой вызова: модель пересказывает дату.
    no_data: bool = False

    @property
    def answered(self) -> bool:
        return not self.is_error or self.no_data


class NoDataError(Exception):
    pass


def is_masked_name(name: str) -> bool:
    # Маскируется только имя с буквой и цифрой. Подпись из одних цифр («15», «2026») читается как
    # число, и страж сверяет её как число: маска вырезала бы её из «15,0%» и «28.09.2026».
    return any(character.isdigit() for character in name) and any(
        character.isalpha() for character in name
    )


def context_config(info: ValidationInfo) -> AppConfig:
    assert info.context is not None
    config: AppConfig = info.context["config"]
    return config


def context_today(info: ValidationInfo) -> date:
    assert info.context is not None
    today: date = info.context["today"]
    return today


def context_earliest_day(info: ValidationInfo) -> date | None:
    assert info.context is not None
    earliest_day: date | None = info.context["earliest_day"]
    return earliest_day


class DayArgument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    day: date

    @field_validator("day", mode="after")
    @classmethod
    def day_is_in_range(cls, value: date, info: ValidationInfo) -> date:
        if value > context_today(info):
            raise ValueError(text("day_in_future", day=date_label(value)))
        earliest_day = context_earliest_day(info)
        if earliest_day is not None and value < earliest_day:
            raise ValueError(text("no_data_before", day=date_label(earliest_day)))
        return value


class MonthArgument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    year: int
    month: Annotated[int, Field(ge=1, le=12)]

    @model_validator(mode="after")
    def month_is_in_range(self, info: ValidationInfo) -> "MonthArgument":
        today = context_today(info)
        if (self.year, self.month) > (today.year, today.month):
            raise ValueError(text("month_in_future", month=f"{self.month:02d}.{self.year}"))
        earliest_day = context_earliest_day(info)
        if earliest_day is not None and (self.year, self.month) < (
            earliest_day.year,
            earliest_day.month,
        ):
            raise ValueError(text("no_data_before", day=f"{earliest_day:%m.%Y}"))
        return self


def period_argument_kind(value: object) -> str:
    if isinstance(value, str):
        return "named"
    if isinstance(value, DayArgument) or (isinstance(value, dict) and "day" in value):
        return "day"
    return "month"


# Discriminator: ошибка аргумента называет только проблему выбранной формы, а не всех трёх.
PeriodArgument = Annotated[
    Annotated[ChatPeriod, Tag("named")]
    | Annotated[DayArgument, Tag("day")]
    | Annotated[MonthArgument, Tag("month")],
    Discriminator(period_argument_kind),
]


def chosen_period(
    argument: ChatPeriod | DayArgument | MonthArgument, today: date
) -> ChatPeriodChoice:
    # Сегодня и текущий месяц читаются как текущие периоды: по правилу закрытых они упёрлись бы
    # в снапшот ещё не наступившего конца.
    if isinstance(argument, DayArgument):
        return "azi" if argument.day == today else ChatDay(argument.day)
    if isinstance(argument, MonthArgument):
        first_day = date(argument.year, argument.month, 1)
        return "luna_curenta" if first_day == today.replace(day=1) else ChatMonth(first_day)
    return argument


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("showroom", mode="after", check_fields=False)
    @classmethod
    def showroom_is_configured(cls, value: str, info: ValidationInfo) -> str:
        config = context_config(info)
        if value != ALL_SHOWROOMS and value not in config.status_mapping.showrooms:
            raise ValueError(text("unknown_showroom", showroom=value))
        return value

    @field_validator("manager", mode="after", check_fields=False)
    @classmethod
    def manager_is_consultant(cls, value: str, info: ValidationInfo) -> str:
        if value != ALL_MANAGERS and value not in consultant_names(context_config(info)):
            raise ValueError(text("unknown_consultant", manager=value))
        return value


class FunnelArguments(ToolArguments):
    period: PeriodArgument
    showroom: str


class ManagerKpiArguments(ToolArguments):
    manager: str
    period: PeriodArgument

    @field_validator("manager", mode="after")
    @classmethod
    def manager_is_named(cls, value: str) -> str:
        if value == ALL_MANAGERS:
            raise ValueError(text("one_consultant_required"))
        return value


class ComparePeriodsArguments(ToolArguments):
    metric: str
    period_a: PeriodArgument
    period_b: PeriodArgument
    showroom: str

    @field_validator("metric", mode="after")
    @classmethod
    def metric_is_known(cls, value: str) -> str:
        if value not in COMPARABLE_METRICS:
            raise ValueError(text("unknown_metric", metric=value))
        return value


class LossReasonsArguments(ToolArguments):
    period: PeriodArgument
    showroom: str


class SourceBreakdownArguments(ToolArguments):
    period: PeriodArgument
    by: BreakdownColumn
    showroom: str


class RepeatClientsArguments(ToolArguments):
    period: PeriodArgument

    @field_validator("period", mode="after")
    @classmethod
    def period_is_month(
        cls, value: ChatPeriod | DayArgument | MonthArgument
    ) -> ChatPeriod | DayArgument | MonthArgument:
        if isinstance(value, DayArgument) or (
            isinstance(value, str) and value not in MONTH_PERIODS
        ):
            raise ValueError(text("repeat_clients_months_only"))
        return value


class ManagerTouchesArguments(ToolArguments):
    period: PeriodArgument
    manager: str

    @field_validator("period", mode="after")
    @classmethod
    def period_is_day_or_week(
        cls, value: ChatPeriod | DayArgument | MonthArgument
    ) -> ChatPeriod | DayArgument | MonthArgument:
        if not (isinstance(value, DayArgument) or value in TOUCH_PERIODS):
            raise ValueError(text("touches_days_only"))
        return value


class OverdueFollowupsArguments(ToolArguments):
    manager: str


class UntouchedLeadsArguments(ToolArguments):
    pass


ARGUMENT_MODELS: dict[ChatToolName, type[ToolArguments]] = {
    "funnel": FunnelArguments,
    "manager_kpi": ManagerKpiArguments,
    "compare_periods": ComparePeriodsArguments,
    "loss_reasons": LossReasonsArguments,
    "overdue_followups": OverdueFollowupsArguments,
    "untouched_leads": UntouchedLeadsArguments,
    "source_breakdown": SourceBreakdownArguments,
    "repeat_clients": RepeatClientsArguments,
    "manager_touches": ManagerTouchesArguments,
}


def consultant_names(config: AppConfig) -> tuple[str, ...]:
    return tuple(
        manager.name
        for manager in config.managers.managers
        if manager.active and not manager.test_account and not manager.not_taken
    )


def enum_property(values: tuple[str, ...]) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def strict_object(properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


# Strict-режим не поддерживает pattern и minimum/maximum: месяц двумя целыми, год и границы дат
# проверяет pydantic.
DAY_PERIOD_FORM = strict_object({"day": {"type": "string", "format": "date"}})
MONTH_PERIOD_FORM = strict_object(
    {
        "year": {"type": "integer"},
        "month": {"type": "integer", "enum": list(range(1, 13))},
    }
)
PERIOD_PROPERTY: dict[str, Any] = {
    "anyOf": [enum_property(CHAT_PERIODS), DAY_PERIOD_FORM, MONTH_PERIOD_FORM]
}
# Инструменты со своим набором периодов получают подмножество: модель не видит формы, которые
# pydantic всё равно отклонит.
TOUCH_PERIOD_PROPERTY: dict[str, Any] = {"anyOf": [enum_property(TOUCH_PERIODS), DAY_PERIOD_FORM]}
MONTH_PERIOD_PROPERTY: dict[str, Any] = {"anyOf": [enum_property(MONTH_PERIODS), MONTH_PERIOD_FORM]}


def tool_definitions(config: AppConfig) -> list[ToolParam]:
    # Необязательные параметры заданы значением «toate»/«toti», а не отсутствием: в strict
    # все свойства перечислены в required.
    showrooms = enum_property((ALL_SHOWROOMS, *config.status_mapping.showrooms))
    periods = PERIOD_PROPERTY
    consultants = consultant_names(config)
    properties: dict[ChatToolName, dict[str, dict[str, Any]]] = {
        "funnel": {"period": periods, "showroom": showrooms},
        "manager_kpi": {"manager": enum_property(consultants), "period": periods},
        "compare_periods": {
            "metric": enum_property(COMPARABLE_METRICS),
            "period_a": periods,
            "period_b": periods,
            "showroom": showrooms,
        },
        "loss_reasons": {"period": periods, "showroom": showrooms},
        "overdue_followups": {"manager": enum_property((ALL_MANAGERS, *consultants))},
        "untouched_leads": {},
        "source_breakdown": {
            "period": periods,
            "by": enum_property(BREAKDOWN_COLUMNS),
            "showroom": showrooms,
        },
        "repeat_clients": {"period": MONTH_PERIOD_PROPERTY},
        "manager_touches": {
            "period": TOUCH_PERIOD_PROPERTY,
            "manager": enum_property((ALL_MANAGERS, *consultants)),
        },
    }
    parameter_descriptions = config.modules.chat.parameters
    return [
        {
            "name": name,
            "description": description,
            "strict": True,
            "input_schema": strict_object(
                {
                    parameter: (
                        {**schema, "description": parameter_descriptions[parameter]}
                        if parameter in parameter_descriptions
                        else schema
                    )
                    for parameter, schema in properties[name].items()
                }
            ),
        }
        for name, description in config.modules.chat.tools.items()
    ]


def date_label(day: date) -> str:
    return f"{day:%d.%m.%Y}"


def days_label(first_day: date, last_day: date) -> str:
    if first_day == last_day:
        return date_label(first_day)
    if first_day.year == last_day.year:
        return f"{first_day:%d.%m}–{date_label(last_day)}"
    return f"{date_label(first_day)}–{date_label(last_day)}"


def period_title(period: ChatPeriodChoice, days: str) -> str:
    if isinstance(period, ChatDay):
        return days
    if isinstance(period, ChatMonth):
        return f"{RO_MONTHS[period.first_day.month - 1]} {period.first_day.year}"
    return f"{days} ({period})"


@dataclass(frozen=True)
class PeriodFrame:
    period: ChatPeriodChoice
    first_day: date
    last_day: date
    label: str
    window: Period
    snapshot_date: date
    frame: pd.DataFrame
    # Снапшот раньше конца периода: цифры частичные, на эту дату.
    data_as_of: date | None
    # Снапшота за последний день закрытого периода нет, взят более поздний.
    missing_snapshot_for: date | None

    @property
    def snapshot_note(self) -> str | None:
        if self.missing_snapshot_for is not None:
            # Подменный снапшот позже конца периода: лиды периода все, на его дату только
            # статусы. Прямым текстом, иначе модель писала «datele pot fi incomplete».
            return text("snapshot_substituted", used=f"{self.snapshot_date:%d.%m}")
        if self.data_as_of is not None:
            return text("snapshot_used", used=date_label(self.snapshot_date))
        return None


async def load_snapshot(data: ToolData, snapshot_date: date) -> pd.DataFrame:
    try:
        return await data.load_frame(snapshot_date)
    except SnapshotMissingError as error:
        raise NoDataError(text("no_snapshot_for", day=date_label(snapshot_date))) from error


def latest_snapshot_date(data: ToolData) -> date:
    if not data.snapshot_dates:
        raise NoDataError(text("no_snapshot_yet"))
    return max(data.snapshot_dates)


async def period_frame(
    data: ToolData, argument: ChatPeriod | DayArgument | MonthArgument
) -> PeriodFrame:
    period = chosen_period(argument, data.today)
    first_day, last_day = period_days(period, data.today)
    label = days_label(first_day, last_day)
    latest_snapshot_date(data)
    snapshot_date = period_snapshot_date(period, data.today, data.snapshot_dates)
    if snapshot_date is None:
        raise NoDataError(text("no_snapshot_for", day=date_label(last_day)))
    if snapshot_date < first_day:
        raise NoDataError(text("no_data_yet", days=label, snapshot=date_label(snapshot_date)))
    return PeriodFrame(
        period,
        first_day,
        last_day,
        label,
        chat_period_window(period, data.today, data.config.status_mapping.time),
        snapshot_date,
        await load_snapshot(data, snapshot_date),
        data_as_of=snapshot_date if snapshot_date < last_day else None,
        missing_snapshot_for=last_day if snapshot_date > last_day else None,
    )


def period_header(period_frame: PeriodFrame) -> dict[str, Any]:
    return {
        "period": (
            period_frame.period
            if isinstance(period_frame.period, str)
            else period_title(period_frame.period, period_frame.label)
        ),
        "days": period_frame.label,
        # Дни считает код: модель, посчитав их сама, называла число, которого нет в результате.
        "day_count": (period_frame.last_day - period_frame.first_day).days + 1,
        "days_with_data": (
            (period_frame.data_as_of or period_frame.last_day) - period_frame.first_day
        ).days
        + 1,
        "snapshot_date": date_label(period_frame.snapshot_date),
        "data_as_of": (
            None if period_frame.data_as_of is None else date_label(period_frame.data_as_of)
        ),
        "missing_snapshot_for": (
            None
            if period_frame.missing_snapshot_for is None
            else date_label(period_frame.missing_snapshot_for)
        ),
        "snapshot_note": period_frame.snapshot_note,
    }


def selected_showroom(showroom: str) -> str | None:
    return None if showroom == ALL_SHOWROOMS else showroom


def funnel_counts(
    period_frame: PeriodFrame, showroom: str | None, config: AppConfig
) -> tuple[LeadCounts, int]:
    frame, window, analysis_date = (
        period_frame.frame,
        period_frame.window,
        period_frame.snapshot_date,
    )
    if showroom is None:
        return (
            lead_counts(frame, window, analysis_date, config),
            converted_count(frame, window),
        )
    return (
        lead_counts_by_showroom(frame, window, analysis_date, config)[showroom],
        converted_count_by_showroom(frame, window, config)[showroom],
    )


def outcome(content: dict[str, Any], *period_frames: PeriodFrame) -> ToolOutcome:
    notes = [frame.snapshot_note for frame in period_frames]
    return ToolOutcome(
        content,
        is_error=False,
        scope=text(
            "period_scope",
            titles=[period_title(frame.period, frame.label) for frame in period_frames],
        ),
        snapshot_dates=tuple(frame.snapshot_date for frame in period_frames),
        snapshot_notes=tuple(dict.fromkeys(note for note in notes if note is not None)),
    )


async def funnel(data: ToolData, arguments: FunnelArguments) -> ToolOutcome:
    frame = await period_frame(data, arguments.period)
    showroom = selected_showroom(arguments.showroom)
    counts, contracts = funnel_counts(frame, showroom, data.config)
    kpis = kpis_from(counts)
    return outcome(
        {
            **period_header(frame),
            "showroom": showroom,
            "counts": {name: getattr(counts, name) for name in COUNT_NAMES},
            "contracts": contracts,
            "kpis": {name: percent_one_decimal(getattr(kpis, name)) for name in KPI_NAMES},
        },
        frame,
    )


async def manager_kpi(data: ToolData, arguments: ManagerKpiArguments) -> ToolOutcome:
    frame = await period_frame(data, arguments.period)
    table = manager_cockpit_table(frame.frame, frame.window, frame.snapshot_date, data.config)
    row = table[table["name"].eq(arguments.manager)].iloc[0]
    targets = data.config.kpi.targets
    meets_target = {name: row[f"{name}_meets_target"] for name in targets}
    return outcome(
        {
            **period_header(frame),
            "manager": arguments.manager,
            "counts": {name: int(row[name]) for name in COUNT_NAMES},
            "kpis": {name: percent_one_decimal(row[name]) for name in KPI_NAMES},
            "targets": {
                name: target_label(data.config.kpi.target_value(name), target.direction)
                for name, target in targets.items()
            },
            "meets_target": meets_target,
            "targets_met": sum(value is True for value in meets_target.values()),
            "targets_total": len(targets),
            "vs_target": {
                name: target_gap(row[name], data.config.kpi.target_value(name)) for name in targets
            },
        },
        frame,
    )


def target_gap(kpi: float | None, target_value: float) -> str | None:
    gap = value_difference(kpi, target_value)
    return None if gap is None else signed_points_one_decimal(gap)


def count_value(
    period_frame: PeriodFrame, metric: str, showroom: str | None, config: AppConfig
) -> int:
    counts, contracts = funnel_counts(period_frame, showroom, config)
    value: int = contracts if metric == CONTRACTS_METRIC else getattr(counts, metric)
    return value


def kpi_value(
    period_frame: PeriodFrame, metric: str, showroom: str | None, config: AppConfig
) -> float | None:
    counts, _ = funnel_counts(period_frame, showroom, config)
    value: float | None = getattr(kpis_from(counts), metric)
    return value


def direction_text(difference: float) -> str:
    if difference > 0:
        return text("direction_up")
    if difference < 0:
        return text("direction_down")
    return text("direction_same")


def count_comparison(count_a: int, count_b: int) -> dict[str, Any]:
    # Значения, направление и разницу формулирует код, модель цитирует: иначе она путает, что с
    # чем сравнивается, считает разницу сама или отвечает разницей без самих значений.
    difference = value_difference(count_a, count_b)
    assert difference is not None
    delta = period_delta(count_a, count_b)
    change = (
        text(
            "change_from_zero",
            value_a=count_a,
            value_b=count_b,
            difference=signed_count(difference),
        )
        if delta is None
        else text(
            "change_with_difference",
            value_a=count_a,
            value_b=count_b,
            difference=signed_count(difference),
            change=signed_percent_one_decimal(delta),
        )
    )
    return {
        "difference": abs(difference),
        "direction": direction_text(difference),
        "change": change,
    }


def kpi_comparison(kpi_a: float | None, kpi_b: float | None) -> dict[str, Any]:
    difference = value_difference(kpi_a, kpi_b)
    if difference is None:
        return {"difference": None, "direction": None, "change": None}
    # Относительное изменение доли читалось бы как изменение в пунктах: только пункты. Направление
    # по округлённой разнице, чтобы «0,0 pp» не шло с «creștere».
    return {
        "difference": points_one_decimal(difference),
        "direction": direction_text(round(difference * 100, 1)),
        "change": text(
            "change_in_points",
            value_a=percent_one_decimal(kpi_a),
            value_b=percent_one_decimal(kpi_b),
            difference=signed_points_one_decimal(difference),
        ),
    }


async def compare_periods(data: ToolData, arguments: ComparePeriodsArguments) -> ToolOutcome:
    frame_a = await period_frame(data, arguments.period_a)
    frame_b = await period_frame(data, arguments.period_b)
    showroom = selected_showroom(arguments.showroom)
    metric = arguments.metric
    values: tuple[int | str, int | str]
    if metric in KPI_NAMES:
        kpi_a = kpi_value(frame_a, metric, showroom, data.config)
        kpi_b = kpi_value(frame_b, metric, showroom, data.config)
        values = (percent_one_decimal(kpi_a), percent_one_decimal(kpi_b))
        comparison = kpi_comparison(kpi_a, kpi_b)
    else:
        count_a = count_value(frame_a, metric, showroom, data.config)
        count_b = count_value(frame_b, metric, showroom, data.config)
        values = (count_a, count_b)
        comparison = count_comparison(count_a, count_b)
    return outcome(
        {
            "metric": metric,
            "showroom": showroom,
            "period_a": {**period_header(frame_a), "value": values[0]},
            "period_b": {**period_header(frame_b), "value": values[1]},
            **comparison,
        },
        frame_a,
        frame_b,
    )


def showroom_reasons(
    losses: LossReasons, showroom: str | None, labels: dict[str, str]
) -> dict[str, dict[str, Any]]:
    return {
        labels[reason]: {
            "lead_count": count,
            "share": percent_one_decimal(losses.showroom_reason_share(showroom, reason)),
        }
        for reason, count in losses.by_showroom[showroom].items()
        if count
    }


async def loss_reasons(data: ToolData, arguments: LossReasonsArguments) -> ToolOutcome:
    frame = await period_frame(data, arguments.period)
    losses = loss_reasons_in_window(frame.frame, frame.window, data.config)
    labels = {
        key: reason.label
        for key, reason in data.config.status_mapping.categories.LOST.reasons.items()
    }
    showroom = selected_showroom(arguments.showroom)
    if showroom is None:
        content: dict[str, Any] = {
            "total": losses.total,
            "by_reason": {
                labels[key]: {
                    "lead_count": losses.reason_total(key),
                    "share": percent_one_decimal(losses.reason_share(key)),
                }
                for key in losses.reasons_by_count
            },
            "showroom_count": sum(
                1 for key in losses.by_showroom if key is not None and losses.showroom_total(key)
            ),
            "by_showroom": {
                (key or text("without_showroom")): {
                    "total": losses.showroom_total(key),
                    "by_reason": showroom_reasons(losses, key, labels),
                }
                for key in losses.by_showroom
                if losses.showroom_total(key)
            },
        }
    else:
        content = {
            "total": losses.showroom_total(showroom),
            "by_reason": showroom_reasons(losses, showroom, labels),
        }
    return outcome({**period_header(frame), "showroom": showroom, **content}, frame)


def breakdown_numbers(counts: LeadCounts) -> dict[str, Any]:
    kpis = kpis_from(counts)
    return {
        "leads": counts.leads,
        "useful": counts.useful,
        "offers": counts.offers,
        "clienti": counts.clienti,
        "irr": percent_one_decimal(kpis.irr),
        "scr": percent_one_decimal(kpis.scr),
    }


def breakdown_label(key: str, by: BreakdownColumn, config: AppConfig) -> str:
    if by != "utm_campanie":
        return key
    # Сырое значение UTM модели не уходит: в нём бывают телефон и e-mail (инвариант 7).
    return campaign_label(
        key,
        config.modules.scr_by_source_campaign_params.campaign_label_max_length,
        config.status_mapping.hidden_campaign_label,
    )


def labeled_rows(
    breakdown: LeadBreakdown, by: BreakdownColumn, config: AppConfig
) -> list[tuple[str, LeadCounts]]:
    # Кампании с одной подписью (скрытые, обрезанные) одной строкой: две строки с одним key
    # модель не различила бы. Порядок тот же, что у lead_breakdown.
    parts: dict[str, list[LeadCounts]] = {}
    for row in breakdown.rows:
        parts.setdefault(breakdown_label(row.key, by, config), []).append(row.counts)
    merged = {label: add_counts(counts) for label, counts in parts.items()}
    return sorted(merged.items(), key=lambda item: (-item[1].leads, item[0]))


@dataclass(frozen=True)
class BreakdownContent:
    content: dict[str, Any]
    labels: tuple[str, ...]


def breakdown_content(
    breakdown: LeadBreakdown, by: BreakdownColumn, config: AppConfig
) -> BreakdownContent:
    rows = labeled_rows(breakdown, by, config)
    other_labels = tuple(
        dict.fromkeys(breakdown_label(key, by, config) for key in breakdown.other_keys)
    )
    content: dict[str, Any] = {
        "by": by,
        "row_count": len(rows),
        "rows": [{"key": label, **breakdown_numbers(counts)} for label, counts in rows],
        # При min_leads = 1 свёртки нет; появится порог, и свёрнутые лиды не пропадут молча.
        "other": (
            None
            if breakdown.other is None
            else {
                "keys": list(other_labels),
                "key_count": len(breakdown.other_keys),
                **breakdown_numbers(breakdown.other.counts),
            }
        ),
        "without_key": (
            None
            if breakdown.without_key is None
            else breakdown_numbers(breakdown.without_key.counts)
        ),
        "total": breakdown_numbers(breakdown.total.counts),
    }
    if by == "utm_campanie":
        content["leads_with_key"] = breakdown.leads_with_key
        content["key_share"] = percent_one_decimal(breakdown.key_share)
    return BreakdownContent(content, (*(label for label, _ in rows), *other_labels))


async def source_breakdown(data: ToolData, arguments: SourceBreakdownArguments) -> ToolOutcome:
    frame = await period_frame(data, arguments.period)
    showroom = selected_showroom(arguments.showroom)
    lead_frame = (
        frame.frame if showroom is None else frame.frame[frame.frame["showroom"].eq(showroom)]
    )
    # min_leads = 1: в чате все ключи, без свёртки (shape chat-new-tools, «Согласовано»).
    breakdown = lead_breakdown(
        lead_frame, arguments.by, frame.window, frame.snapshot_date, data.config, min_leads=1
    )
    result = breakdown_content(breakdown, arguments.by, data.config)
    return replace(
        outcome({**period_header(frame), "showroom": showroom, **result.content}, frame),
        masked_names=tuple(label for label in result.labels if is_masked_name(label)),
    )


NO_REPEAT_CLIENTS = RepeatClientCounts(clients=0, repeat=0)


def repeat_numbers(counts: RepeatClientCounts) -> dict[str, Any]:
    return {
        "clients": counts.clients,
        "repeat": counts.repeat,
        "share": percent_one_decimal(counts.share),
    }


async def repeat_clients(data: ToolData, arguments: RepeatClientsArguments) -> ToolOutcome:
    frame = await period_frame(data, arguments.period)
    # Месяц от первого дня периода, а не от snapshot_date: подменный снапшот следующего месяца
    # сдвинул бы окно.
    repeat = monthly_repeat_clients(frame.frame, frame.first_day, data.config)
    # Шоурумы конфига всегда, с нулями: «0 в Cluj» это ответ, а не отсутствие данных. Прочие
    # (без шоурума, вне конфига) только с клиентами.
    configured = data.config.status_mapping.showrooms
    showrooms: dict[str | None, RepeatClientCounts] = {
        showroom: repeat.by_showroom.get(showroom, NO_REPEAT_CLIENTS) for showroom in configured
    }
    showrooms.update(
        (showroom, counts)
        for showroom, counts in repeat.by_showroom.items()
        if showroom not in configured and counts.clients
    )
    return outcome(
        {
            **period_header(frame),
            **repeat_numbers(repeat.company),
            "by_reason": [
                {"reason": text("repeat_by_contact"), "client_count": repeat.company.by_contact},
                {
                    "reason": ", ".join(data.config.status_mapping.sources.repeat_client),
                    "client_count": repeat.company.by_source,
                },
            ],
            "by_showroom": [
                {
                    "showroom": showroom or text("without_showroom"),
                    **repeat_numbers(counts),
                    "by_contact": counts.by_contact,
                    "by_source": counts.by_source,
                }
                for showroom, counts in showrooms.items()
            ],
            "showroom_count": sum(
                1 for showroom, counts in showrooms.items() if showroom and counts.clients
            ),
            "won_without_converted_at": repeat.won_without_converted_at,
        },
        frame,
    )


def touch_levels(by_level: tuple[int, ...], config: AppConfig) -> list[dict[str, Any]]:
    # Полные имена статусов, не «R1»: модель, сокращавшая их до «Revenire 1/2/3», писала цифры,
    # которых нет в результате.
    statuses = config.status_mapping.categories.ACTIVE_FOLLOWUP.statuses
    return [
        {"status": status, "touch_count": count}
        for status, count in zip(statuses, by_level, strict=True)
    ]


def touches_content(touches: ManagerTouches, manager: str, config: AppConfig) -> dict[str, Any]:
    if manager != ALL_MANAGERS:
        return {
            "manager": manager,
            "touch_count": touches.touch_count,
            "by_level": touch_levels(touches.by_level, config),
        }
    named = [group for group in touches.groups if group.manager_name is not None]
    return {
        "touch_count": touches.touch_count,
        "by_level": touch_levels(touches.by_level, config),
        "manager_count": sum(1 for group in named if group.touch_count),
        "managers_without_touches": sum(1 for group in named if not group.touch_count),
        "by_manager": [
            {
                "manager": manager_label(group.manager_name),
                "touch_count": group.touch_count,
                "by_level": touch_levels(group.by_level, config),
            }
            for group in touches.groups
        ],
    }


async def manager_touches_tool(data: ToolData, arguments: ManagerTouchesArguments) -> ToolOutcome:
    # Та же цепочка пар снапшотов и те же дни, что w14 (docs/kpi-definitions.md, «Касания
    # консультантов»): за прошлую неделю число совпадает с воскресным отчётом по построению.
    period = chosen_period(arguments.period, data.today)
    first_day, last_day = period_days(period, data.today)
    label = days_label(first_day, last_day)
    day_count = (last_day - first_day).days + 1
    days = tuple(first_day + timedelta(days=offset) for offset in range(day_count))
    chain_dates = touch_snapshot_dates(data.snapshot_dates, first_day, last_day)
    snapshots = tuple(
        [PreviousSnapshot(day, await load_snapshot(data, day)) for day in chain_dates]
    )
    touches = manager_touches(snapshots, days, data.config)
    covered_from = touches.covered_from
    if covered_from is None:
        raise NoDataError(text("touches_no_snapshot_pair", days=label))
    covered_until = chain_dates[-1]
    missing_days = touches.days_without_snapshot
    missing = {"days": day_ranges_label(missing_days), "day_count": len(missing_days)}
    first_covered = f"{covered_from:%d.%m}"
    # Те же сноски, что у w14 (templates/manager_touches.j2), плюс конец данных: неделя чата
    # бывает текущей.
    notes = []
    if covered_from > first_day and covered_from in missing_days:
        notes.append(text("touches_counted_from_and_missing", day=first_covered, **missing))
    else:
        if covered_from > first_day:
            notes.append(text("touches_counted_from", day=first_covered))
        if missing_days:
            notes.append(text("touches_missing_days", **missing))
    if covered_until < last_day:
        notes.append(text("touches_covered_until", day=f"{covered_until:%d.%m}"))
    if arguments.manager != ALL_MANAGERS:
        touches = touches.of_manager(arguments.manager)
    return ToolOutcome(
        {
            "period": period if isinstance(period, str) else period_title(period, label),
            "days": label,
            "day_count": day_count,
            "covered_from": date_label(covered_from),
            "covered_until": date_label(covered_until),
            "covered_day_count": (covered_until - covered_from).days + 1,
            "days_without_snapshot": missing["days"] if missing_days else None,
            "days_without_snapshot_count": len(missing_days),
            **touches_content(touches, arguments.manager, data.config),
        },
        is_error=False,
        scope=text("period_scope", titles=[period_title(period, label)]),
        snapshot_dates=chain_dates,
        snapshot_notes=tuple(notes),
    )


def snapshot_outcome(
    content: dict[str, Any], snapshot_date: date, lead_ids: tuple[int, ...]
) -> ToolOutcome:
    return ToolOutcome(
        {"snapshot_date": date_label(snapshot_date), **content},
        is_error=False,
        scope=text("snapshot_scope", day=date_label(snapshot_date)),
        snapshot_dates=(snapshot_date,),
        lead_ids=lead_ids,
    )


def manager_label(manager_name: str | None) -> str:
    return manager_name or text("not_taken")


def followup_statuses(config: AppConfig) -> list[str]:
    # Те же статусы, что флаг is_followup_status кадра (metrics/frame.py). Имена целиком: модель,
    # сокращавшая их до «Revenire 1/2/3», писала цифры, которых нет в результате.
    categories = config.status_mapping.categories
    return [
        *categories.ACTIVE_FOLLOWUP.statuses,
        *(
            status
            for reason in categories.LOST.reasons.values()
            if reason.followup_field is not None
            for status in reason.statuses
        ),
    ]


def consultant_group_count(manager_names: Iterable[str | None]) -> int:
    return sum(1 for manager_name in manager_names if manager_name is not None)


def missing_followup_content(
    missing: MissingFollowupDate, by_manager: bool, config: AppConfig
) -> dict[str, Any]:
    # Поле не прочитано у всех лидов: без цифры, иначе модель назвала бы «0 без даты».
    if missing.field_unavailable:
        return {"unavailable": True}
    content: dict[str, Any] = {
        "statuses": followup_statuses(config),
        "lead_count": missing.lead_count,
    }
    if by_manager:
        content["manager_count"] = consultant_group_count(
            group.manager_name for group in missing.groups
        )
        content["by_manager"] = [
            {"manager": manager_label(group.manager_name), "lead_count": group.lead_count}
            for group in missing.groups
        ]
    return content


async def overdue_followups(data: ToolData, arguments: OverdueFollowupsArguments) -> ToolOutcome:
    snapshot_date = latest_snapshot_date(data)
    frame = await load_snapshot(data, snapshot_date)
    overdue = overdue_revenire_by_manager(frame, snapshot_date, data.config)
    # Как в d3: без параметра missing_followup_date поле необязательно, «без даты» не вопрос.
    missing = (
        missing_followup_date(frame, snapshot_date, data.config)
        if data.config.modules.overdue_revenire_params.missing_followup_date
        else None
    )
    all_managers = arguments.manager == ALL_MANAGERS
    if not all_managers:
        overdue = overdue.of_manager(arguments.manager)
        missing = None if missing is None else missing.of_manager(arguments.manager)
    content: dict[str, Any] = (
        {
            "lead_count": overdue.lead_count,
            "max_days_overdue": overdue.max_days_overdue,
            "manager_count": consultant_group_count(group.manager_name for group in overdue.groups),
            "by_manager": [
                {
                    "manager": manager_label(group.manager_name),
                    "lead_count": group.lead_count,
                    "max_days_overdue": group.max_days_overdue,
                }
                for group in overdue.groups
            ],
        }
        if all_managers
        else {
            "manager": arguments.manager,
            "lead_count": overdue.lead_count,
            "max_days_overdue": overdue.max_days_overdue,
        }
    )
    lead_ids = overdue.lead_ids
    if missing is not None:
        content["missing_followup_date"] = missing_followup_content(
            missing, all_managers, data.config
        )
        lead_ids = (*lead_ids, *missing.lead_ids)
    return snapshot_outcome(content, snapshot_date, lead_ids)


async def untouched_leads_tool(data: ToolData, arguments: UntouchedLeadsArguments) -> ToolOutcome:
    snapshot_date = latest_snapshot_date(data)
    frame = await load_snapshot(data, snapshot_date)
    untouched = untouched_leads(frame, snapshot_date, data.config)
    params = data.config.modules.untouched_leads_params
    return snapshot_outcome(
        {
            "threshold_hours": params.threshold_hours,
            "lookback_days": params.lookback_days,
            "lead_count": untouched.lead_count,
            "oldest_age_hours": untouched.oldest_age_hours,
            "oldest_age_days": (
                None if untouched.oldest_age_hours is None else untouched.oldest_age_hours // 24
            ),
            "manager_count": consultant_group_count(
                group.manager_name for group in untouched.groups
            ),
            "by_manager": [
                {
                    "manager": manager_label(group.manager_name),
                    "lead_count": group.lead_count,
                    "oldest_age_hours": group.oldest_age_hours,
                }
                for group in untouched.groups
            ],
        },
        snapshot_date,
        untouched.lead_ids,
    )


ToolFunction = Callable[[ToolData, Any], Awaitable[ToolOutcome]]
TOOL_FUNCTIONS: Mapping[ChatToolName, ToolFunction] = {
    "funnel": funnel,
    "manager_kpi": manager_kpi,
    "compare_periods": compare_periods,
    "loss_reasons": loss_reasons,
    "overdue_followups": overdue_followups,
    "untouched_leads": untouched_leads_tool,
    "source_breakdown": source_breakdown,
    "repeat_clients": repeat_clients,
    "manager_touches": manager_touches_tool,
}


async def run_tool(name: str, arguments: object, data: ToolData) -> ToolOutcome:
    # Аргументы tool-call это граница (PRINCIPLES «Ошибки и валидация»): модель может прислать
    # что угодно, ошибка возвращается ей как tool_result с is_error.
    if name not in data.config.modules.chat.tools:
        return ToolOutcome({"error": text("unknown_tool", name=name)}, is_error=True)
    try:
        validated = ARGUMENT_MODELS[name].model_validate(
            arguments,
            context={
                "config": data.config,
                "today": data.today,
                "earliest_day": (
                    earliest_specific_day(
                        data.snapshot_dates[0], data.config.modules.chat.specific_date_history_years
                    )
                    if data.snapshot_dates
                    else None
                ),
            },
        )
        return await TOOL_FUNCTIONS[name](data, validated)
    except ValidationError as error:
        problems = "; ".join(str(problem["msg"]) for problem in error.errors())
        return ToolOutcome({"error": text("invalid_arguments", problems=problems)}, is_error=True)
    except NoDataError as error:
        return ToolOutcome({"error": str(error)}, is_error=True, no_data=True)
