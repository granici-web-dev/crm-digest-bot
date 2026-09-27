from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd
from anthropic.types import ToolParam
from pydantic import BaseModel, ConfigDict, ValidationError, ValidationInfo, field_validator

from digest.config import KPI_NAMES, AppConfig, ChatToolName
from digest.db.lead_frame import SnapshotMissingError
from digest.metrics.chat_periods import (
    CHAT_PERIODS,
    ChatPeriod,
    named_period_window,
    period_days,
    period_snapshot_date,
)
from digest.metrics.cockpit import manager_cockpit_table
from digest.metrics.daily_checks import overdue_revenire_by_manager, untouched_leads
from digest.metrics.extra import period_delta
from digest.metrics.kpi import (
    COUNT_NAMES,
    LeadCounts,
    Period,
    kpis_from,
    lead_counts,
    lead_counts_by_showroom,
)
from digest.metrics.weekly import (
    converted_count,
    converted_count_by_showroom,
    loss_reasons_in_window,
)
from digest.reports.render import change_label, percent_one_decimal, target_label

ALL_SHOWROOMS = "toate"
ALL_MANAGERS = "toti"
NOT_TAKEN_LABEL = "Nepreluate"
WITHOUT_SHOWROOM_LABEL = "fără showroom"
CONTRACTS_METRIC = "contracts"
COMPARABLE_METRICS: tuple[str, ...] = (*COUNT_NAMES, *KPI_NAMES, CONTRACTS_METRIC)

FrameLoader = Callable[[date], Awaitable[pd.DataFrame]]


@dataclass(frozen=True)
class ToolData:
    today: date
    # Даты успешных снапшотов по возрастанию.
    snapshot_dates: tuple[date, ...]
    # Бросает SnapshotMissingError, если за дату нет успешного снапшота.
    load_frame: FrameLoader
    config: AppConfig


@dataclass(frozen=True)
class ToolOutcome:
    content: dict[str, Any]
    is_error: bool
    # Начало строки подписи: период(ы) или дата снапшота; имя функции дописывает цикл.
    scope: str | None = None
    snapshot_dates: tuple[date, ...] = ()
    # Строки подписи о снапшоте, если он не за последний день периода.
    snapshot_notes: tuple[str, ...] = ()


class NoDataError(Exception):
    pass


def context_config(info: ValidationInfo) -> AppConfig:
    assert info.context is not None
    config: AppConfig = info.context["config"]
    return config


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    @field_validator("showroom", mode="after", check_fields=False)
    @classmethod
    def showroom_is_configured(cls, value: str, info: ValidationInfo) -> str:
        config = context_config(info)
        if value != ALL_SHOWROOMS and value not in config.status_mapping.showrooms:
            raise ValueError(f"showroom necunoscut: {value}")
        return value

    @field_validator("manager", mode="after", check_fields=False)
    @classmethod
    def manager_is_consultant(cls, value: str, info: ValidationInfo) -> str:
        if value != ALL_MANAGERS and value not in consultant_names(context_config(info)):
            raise ValueError(f"consultant necunoscut: {value}")
        return value


class FunnelArguments(ToolArguments):
    period: ChatPeriod
    showroom: str


class ManagerKpiArguments(ToolArguments):
    manager: str
    period: ChatPeriod

    @field_validator("manager", mode="after")
    @classmethod
    def manager_is_named(cls, value: str) -> str:
        if value == ALL_MANAGERS:
            raise ValueError("manager_kpi cere un singur consultant")
        return value


class ComparePeriodsArguments(ToolArguments):
    metric: str
    period_a: ChatPeriod
    period_b: ChatPeriod
    showroom: str

    @field_validator("metric", mode="after")
    @classmethod
    def metric_is_known(cls, value: str) -> str:
        if value not in COMPARABLE_METRICS:
            raise ValueError(f"metrică necunoscută: {value}")
        return value


class LossReasonsArguments(ToolArguments):
    period: ChatPeriod
    showroom: str


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
}


def consultant_names(config: AppConfig) -> tuple[str, ...]:
    return tuple(
        manager.name
        for manager in config.managers.managers
        if manager.active and not manager.test_account and not manager.not_taken
    )


def enum_property(values: tuple[str, ...]) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def tool_definitions(config: AppConfig) -> list[ToolParam]:
    # Необязательные параметры заданы значением «toate»/«toti», а не отсутствием: в strict
    # все свойства перечислены в required.
    showrooms = enum_property((ALL_SHOWROOMS, *config.status_mapping.showrooms))
    periods = enum_property(CHAT_PERIODS)
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
    }
    return [
        {
            "name": name,
            "description": description,
            "strict": True,
            "input_schema": {
                "type": "object",
                "properties": properties[name],
                "required": list(properties[name]),
                "additionalProperties": False,
            },
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


@dataclass(frozen=True)
class PeriodFrame:
    period: ChatPeriod
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
        used = date_label(self.snapshot_date)
        if self.missing_snapshot_for is not None:
            missing = date_label(self.missing_snapshot_for)
            return f"Date din snapshotul din {used} (nu există snapshot pentru {missing})"
        if self.data_as_of is not None:
            return f"Date din snapshotul din {used}"
        return None


async def load_snapshot(data: ToolData, snapshot_date: date) -> pd.DataFrame:
    try:
        return await data.load_frame(snapshot_date)
    except SnapshotMissingError as error:
        raise NoDataError(f"Nu există snapshot pentru {date_label(snapshot_date)}.") from error


NO_SNAPSHOT_TEXT = "Nu există încă niciun snapshot."


def latest_snapshot_date(data: ToolData) -> date:
    if not data.snapshot_dates:
        raise NoDataError(NO_SNAPSHOT_TEXT)
    return max(data.snapshot_dates)


async def period_frame(data: ToolData, period: ChatPeriod) -> PeriodFrame:
    first_day, last_day = period_days(period, data.today)
    label = days_label(first_day, last_day)
    snapshot_date = period_snapshot_date(period, data.today, data.snapshot_dates)
    if snapshot_date is None:
        if not data.snapshot_dates:
            raise NoDataError(NO_SNAPSHOT_TEXT)
        raise NoDataError(f"Nu există snapshot pentru {date_label(last_day)}.")
    if snapshot_date < first_day:
        raise NoDataError(
            f"Nu există încă date pentru {label}: ultimul snapshot este din "
            f"{date_label(snapshot_date)}."
        )
    return PeriodFrame(
        period,
        label,
        named_period_window(period, data.today, data.config.status_mapping.time),
        snapshot_date,
        await load_snapshot(data, snapshot_date),
        data_as_of=snapshot_date if snapshot_date < last_day else None,
        missing_snapshot_for=last_day if snapshot_date > last_day else None,
    )


def period_header(period_frame: PeriodFrame) -> dict[str, Any]:
    return {
        "period": period_frame.period,
        "days": period_frame.label,
        "snapshot_date": date_label(period_frame.snapshot_date),
        "data_as_of": (
            None if period_frame.data_as_of is None else date_label(period_frame.data_as_of)
        ),
        "missing_snapshot_for": (
            None
            if period_frame.missing_snapshot_for is None
            else date_label(period_frame.missing_snapshot_for)
        ),
    }


def counts_content(counts: LeadCounts) -> dict[str, int]:
    return {name: getattr(counts, name) for name in COUNT_NAMES}


def kpis_content(counts: LeadCounts) -> dict[str, str]:
    kpis = kpis_from(counts)
    return {name: percent_one_decimal(getattr(kpis, name)) for name in KPI_NAMES}


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
        scope="Perioada: "
        + " vs ".join(f"{frame.label} ({frame.period})" for frame in period_frames),
        snapshot_dates=tuple(frame.snapshot_date for frame in period_frames),
        snapshot_notes=tuple(dict.fromkeys(note for note in notes if note is not None)),
    )


async def funnel(data: ToolData, arguments: FunnelArguments) -> ToolOutcome:
    frame = await period_frame(data, arguments.period)
    showroom = selected_showroom(arguments.showroom)
    counts, contracts = funnel_counts(frame, showroom, data.config)
    return outcome(
        {
            **period_header(frame),
            "showroom": showroom,
            "counts": counts_content(counts),
            "contracts": contracts,
            "kpis": kpis_content(counts),
        },
        frame,
    )


async def manager_kpi(data: ToolData, arguments: ManagerKpiArguments) -> ToolOutcome:
    frame = await period_frame(data, arguments.period)
    table = manager_cockpit_table(frame.frame, frame.window, frame.snapshot_date, data.config)
    row = table[table["name"].eq(arguments.manager)].iloc[0]
    targets = data.config.kpi.targets
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
            "meets_target": {name: row[f"{name}_meets_target"] for name in targets},
        },
        frame,
    )


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


async def compare_periods(data: ToolData, arguments: ComparePeriodsArguments) -> ToolOutcome:
    frame_a = await period_frame(data, arguments.period_a)
    frame_b = await period_frame(data, arguments.period_b)
    showroom = selected_showroom(arguments.showroom)
    metric = arguments.metric
    values: tuple[int | str, int | str]
    if metric in KPI_NAMES:
        values = (
            percent_one_decimal(kpi_value(frame_a, metric, showroom, data.config)),
            percent_one_decimal(kpi_value(frame_b, metric, showroom, data.config)),
        )
        # Относительное изменение доли читалось бы как изменение в пунктах: не отдаём.
        change = None
    else:
        count_a = count_value(frame_a, metric, showroom, data.config)
        count_b = count_value(frame_b, metric, showroom, data.config)
        values = (count_a, count_b)
        change = change_label(period_delta(count_a, count_b))
    return outcome(
        {
            "metric": metric,
            "showroom": showroom,
            "period_a": {**period_header(frame_a), "value": values[0]},
            "period_b": {**period_header(frame_b), "value": values[1]},
            "change_a_vs_b": change,
        },
        frame_a,
        frame_b,
    )


def nonzero_reasons(by_reason: dict[str, int], labels: dict[str, str]) -> dict[str, int]:
    return {labels[reason]: count for reason, count in by_reason.items() if count}


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
            "by_reason": {labels[key]: losses.reason_total(key) for key in losses.reasons_by_count},
            "by_showroom": {
                (key or WITHOUT_SHOWROOM_LABEL): {
                    "total": losses.showroom_total(key),
                    "by_reason": {
                        labels[reason]: count for reason, count in by_reason.items() if count
                    },
                }
                for key, by_reason in losses.by_showroom.items()
                if losses.showroom_total(key)
            },
        }
    else:
        by_reason = losses.by_showroom[showroom]
        content = {
            "total": sum(by_reason.values()),
            "by_reason": nonzero_reasons(by_reason, labels),
        }
    return outcome({**period_header(frame), "showroom": showroom, **content}, frame)


def snapshot_outcome(content: dict[str, Any], snapshot_date: date) -> ToolOutcome:
    return ToolOutcome(
        {"snapshot_date": date_label(snapshot_date), **content},
        is_error=False,
        scope=f"Snapshot: {date_label(snapshot_date)}",
        snapshot_dates=(snapshot_date,),
    )


def manager_label(manager_name: str | None) -> str:
    return manager_name or NOT_TAKEN_LABEL


async def overdue_followups(data: ToolData, arguments: OverdueFollowupsArguments) -> ToolOutcome:
    snapshot_date = latest_snapshot_date(data)
    frame = await load_snapshot(data, snapshot_date)
    overdue = overdue_revenire_by_manager(frame, snapshot_date, data.config)
    groups = [
        {
            "manager": manager_label(group.manager_name),
            "lead_count": group.lead_count,
            "max_days_overdue": group.max_days_overdue,
        }
        for group in overdue.groups
        if arguments.manager in (ALL_MANAGERS, group.manager_name)
    ]
    if arguments.manager == ALL_MANAGERS:
        content: dict[str, Any] = {
            "lead_count": overdue.lead_count,
            "max_days_overdue": overdue.max_days_overdue,
            "by_manager": groups,
        }
    else:
        content = {
            "manager": arguments.manager,
            "lead_count": groups[0]["lead_count"] if groups else 0,
            "max_days_overdue": groups[0]["max_days_overdue"] if groups else None,
        }
    return snapshot_outcome(content, snapshot_date)


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
    )


ToolFunction = Callable[[ToolData, Any], Awaitable[ToolOutcome]]
TOOL_FUNCTIONS: Mapping[ChatToolName, ToolFunction] = {
    "funnel": funnel,
    "manager_kpi": manager_kpi,
    "compare_periods": compare_periods,
    "loss_reasons": loss_reasons,
    "overdue_followups": overdue_followups,
    "untouched_leads": untouched_leads_tool,
}


async def run_tool(name: str, arguments: object, data: ToolData) -> ToolOutcome:
    # Аргументы tool-call это граница (PRINCIPLES «Ошибки и валидация»): модель может прислать
    # что угодно, ошибка возвращается ей как tool_result с is_error.
    if name not in data.config.modules.chat.tools:
        return ToolOutcome({"error": f"Instrument necunoscut: {name}."}, is_error=True)
    try:
        validated = ARGUMENT_MODELS[name].model_validate(arguments, context={"config": data.config})
        return await TOOL_FUNCTIONS[name](data, validated)
    except ValidationError as error:
        problems = "; ".join(str(problem["msg"]) for problem in error.errors())
        return ToolOutcome({"error": f"Argumente invalide: {problems}."}, is_error=True)
    except NoDataError as error:
        return ToolOutcome({"error": str(error)}, is_error=True)
