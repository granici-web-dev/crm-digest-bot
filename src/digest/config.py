from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Annotated, Any, Literal, Self, get_args

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    PositiveFloat,
    PositiveInt,
    PrivateAttr,
    ValidationError,
    model_validator,
)

SourceCode = Literal["A", "B", "C", "D", "E", "F", "G", "H"]
Share = Annotated[float, Field(ge=0, le=1)]

# Ключи причин, на которые ссылается metrics/: контракт между status-mapping.yaml и кодом.
# Это наши имена категорий, а не статусы mefi (PRINCIPLES.md, «Комментарии и имена»).
LOSS_REASONS_USED_BY_METRICS = ("IRELEVANT", "NU_RASPUNS", "BUGET", "PRODUS_NEPOTRIVIT", "STAND_BY")


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True)
class LeadCategory:
    category: str
    loss_reason: str | None = None


UNMAPPED = LeadCategory("UNMAPPED")


class WonCategory(StrictConfigModel):
    statuses: list[str]
    also_when: Literal["converted_at_not_null"]


class ActiveCategory(StrictConfigModel):
    statuses: list[str]


class ActiveFollowupCategory(StrictConfigModel):
    statuses: list[str]
    counts_as: Literal["ACTIVE"]


class LossReason(StrictConfigModel):
    statuses: list[str]
    followup_field: str | None = None
    note: str | None = None
    excluded_from_useful: bool = False


class LostCategory(StrictConfigModel):
    reasons: dict[str, LossReason]

    @model_validator(mode="after")
    def reasons_used_by_metrics_exist(self) -> Self:
        missing = [name for name in LOSS_REASONS_USED_BY_METRICS if name not in self.reasons]
        if missing:
            raise ValueError(f"LOST.reasons: нет причин {missing}, на них ссылается metrics/")
        return self


class PartnershipCategory(StrictConfigModel):
    statuses: list[str]
    excluded_from_leads: bool


class Categories(StrictConfigModel):
    WON: WonCategory
    ACTIVE: ActiveCategory
    ACTIVE_FOLLOWUP: ActiveFollowupCategory
    LOST: LostCategory
    PARTNERSHIP: PartnershipCategory


class CustomFieldRef(StrictConfigModel):
    field_id: int
    name: str


class OfertatField(CustomFieldRef):
    ofertat_yes: str
    ofertat_no: str


class CustomFields(StrictConfigModel):
    showroom: CustomFieldRef
    ofertat: OfertatField
    data_revenire: CustomFieldRef
    revenire_notes: list[CustomFieldRef]
    informatii: CustomFieldRef
    mesaj: CustomFieldRef
    utm: list[CustomFieldRef]


class WorkingHours(StrictConfigModel):
    start: time
    end: time


class TimeSettings(StrictConfigModel):
    timezone: Literal["Europe/Bucharest"]
    daily_window_end: time
    working_hours: WorkingHours


class CompletenessThresholds(StrictConfigModel):
    max_missing_leads: int
    max_missing_share: float
    max_skipped_leads: int
    max_skipped_share: float


class SnapshotSettings(StrictConfigModel):
    completeness: CompletenessThresholds


class SourceGroups(StrictConfigModel):
    showroom_visit: list[str]
    showroom_visit_status: str
    web: list[str]
    phone: list[str]
    whatsapp: list[str]
    partner: list[str]
    other: list[str]


class StatusMapping(StrictConfigModel):
    categories: Categories
    custom_fields: CustomFields
    sources: SourceGroups
    showrooms: list[str]
    time: TimeSettings
    raw_strip: list[str]
    raw_known_keys: frozenset[str]
    snapshot: SnapshotSettings

    _category_by_status: dict[str, LeadCategory] = PrivateAttr()

    @model_validator(mode="after")
    def each_status_belongs_to_one_category(self) -> Self:
        categories = self.categories
        pairs: list[tuple[str, LeadCategory]] = [
            *((status, LeadCategory("WON")) for status in categories.WON.statuses),
            *((status, LeadCategory("ACTIVE")) for status in categories.ACTIVE.statuses),
            *(
                (status, LeadCategory("ACTIVE_FOLLOWUP"))
                for status in categories.ACTIVE_FOLLOWUP.statuses
            ),
            *(
                (status, LeadCategory("LOST", reason_name))
                for reason_name, reason in categories.LOST.reasons.items()
                for status in reason.statuses
            ),
            *((status, LeadCategory("PARTNERSHIP")) for status in categories.PARTNERSHIP.statuses),
        ]
        category_by_status: dict[str, LeadCategory] = {}
        for status, category in pairs:
            if status in category_by_status:
                raise ValueError(
                    f"статус {status!r} указан в двух категориях: "
                    f"{category_by_status[status]} и {category}"
                )
            category_by_status[status] = category
        if self.sources.showroom_visit_status not in category_by_status:
            raise ValueError(
                f"sources.showroom_visit_status {self.sources.showroom_visit_status!r} "
                "не указан ни в одной категории"
            )
        self._category_by_status = category_by_status
        return self

    @property
    def category_by_status(self) -> dict[str, LeadCategory]:
        return self._category_by_status


class DataSource(StrictConfigModel):
    title: str
    connected: bool


KpiStatus = Literal["provisional", "calibrated"]


class ReportModule(StrictConfigModel):
    name: str
    sources: list[SourceCode]
    enabled: bool
    params: dict[str, JsonValue] = {}
    requires_kpi_status: Literal["calibrated"] | None = None


class UntouchedLeadsParams(StrictConfigModel):
    threshold_hours: PositiveInt
    lookback_days: PositiveInt


class AnomalyParams(StrictConfigModel):
    site_zero_min_average: PositiveFloat
    irelevant_spike_min: PositiveInt


# Параметры проверяются при загрузке: опечатка в пороге роняет старт, а не отчёт в 19:30.
PARAMS_MODEL_BY_MODULE_NAME: dict[str, type[StrictConfigModel]] = {
    "untouched_leads": UntouchedLeadsParams,
    "anomalies": AnomalyParams,
}


class ChatSettings(StrictConfigModel):
    enabled: bool
    tools: list[str]
    trigger: list[Literal["mention", "reply"]]


class ModuleRegistry(StrictConfigModel):
    sources: dict[SourceCode, DataSource]
    daily: dict[str, ReportModule]
    weekly: dict[str, ReportModule]
    monthly: dict[str, ReportModule]
    yearly: dict[str, ReportModule]
    chat: ChatSettings

    @property
    def all_modules(self) -> dict[str, ReportModule]:
        return {**self.daily, **self.weekly, **self.monthly, **self.yearly}

    def module_named(self, name: str) -> ReportModule:
        return next(module for module in self.all_modules.values() if module.name == name)

    def untouched_leads_params(self) -> UntouchedLeadsParams:
        return UntouchedLeadsParams.model_validate(self.module_named("untouched_leads").params)

    def anomaly_params(self) -> AnomalyParams:
        return AnomalyParams.model_validate(self.module_named("anomalies").params)

    @model_validator(mode="after")
    def enabled_modules_have_connected_sources(self) -> Self:
        all_modules = self.all_modules
        module_count = sum(
            len(level) for level in (self.daily, self.weekly, self.monthly, self.yearly)
        )
        if len(all_modules) != module_count:
            raise ValueError("id модуля повторяется в разных отчётах")
        for module_id, module in all_modules.items():
            undeclared = [code for code in module.sources if code not in self.sources]
            if undeclared:
                raise ValueError(f"модуль {module_id}: источники {undeclared} не описаны в sources")
            disconnected = [code for code in module.sources if not self.sources[code].connected]
            if module.enabled and disconnected:
                raise ValueError(
                    f"модуль {module_id} включён, но источники {disconnected} не подключены"
                )
            params_model = PARAMS_MODEL_BY_MODULE_NAME.get(module.name)
            if params_model is not None:
                try:
                    params_model.model_validate(module.params)
                except ValidationError as error:
                    raise ValueError(f"модуль {module_id}: неверные params: {error}") from error
        return self


class Manager(StrictConfigModel):
    id: int
    name: str
    showroom: str | None
    active: bool
    test_account: bool = False
    # Лид на таком id никто не взял в работу (Desemnat по умолчанию в mefi), см. d2.
    not_taken: bool = False


class ManagerRoster(StrictConfigModel):
    managers: list[Manager]

    @model_validator(mode="after")
    def manager_ids_are_unique(self) -> Self:
        ids = [manager.id for manager in self.managers]
        duplicates = sorted({manager_id for manager_id in ids if ids.count(manager_id) > 1})
        if duplicates:
            raise ValueError(f"id консультантов повторяются: {duplicates}")
        return self

    @model_validator(mode="after")
    def not_taken_managers_are_inactive(self) -> Self:
        selling = [manager.id for manager in self.managers if manager.not_taken and manager.active]
        if selling:
            raise ValueError(f"консультанты {selling}: not_taken только при active: false")
        return self

    @property
    def not_taken_ids(self) -> set[int]:
        return {manager.id for manager in self.managers if manager.not_taken}


KpiName = Literal["scr", "l2o", "o2c", "cdr", "plr", "sc", "pfr", "acr", "irr"]
KPI_NAMES: tuple[KpiName, ...] = get_args(KpiName)
# docs/kpi-definitions.md, «Баллы»: IRR даёт штраф irr_penalty, L2O и O2C баллов не дают.
SCORED_KPI_NAMES: tuple[KpiName, ...] = ("scr", "cdr", "plr", "sc", "pfr", "acr")
Direction = Literal["higher", "lower"]


class ScoreSteps(StrictConfigModel):
    direction: Direction
    steps: list[tuple[str, int]]
    otherwise: int


class RecommendationRule(StrictConfigModel):
    key: str
    kpi: KpiName
    above: str | None = None
    below: str | None = None

    @model_validator(mode="after")
    def exactly_one_comparison(self) -> Self:
        if (self.above is None) == (self.below is None):
            raise ValueError(f"рекомендация {self.key}: нужно ровно одно из above, below")
        return self


class KpiTarget(StrictConfigModel):
    direction: Direction
    threshold: str


class SpiLevel(StrictConfigModel):
    name: str
    min_spi: int


class KpiSettings(StrictConfigModel):
    status: KpiStatus
    thresholds: dict[str, Share]
    active_offer_stale_days: PositiveInt
    levels: list[SpiLevel]
    scores: dict[KpiName, ScoreSteps]
    irr_penalty: ScoreSteps
    recommendations: list[RecommendationRule]
    recommendation_otherwise: str
    targets: dict[KpiName, KpiTarget]

    @model_validator(mode="after")
    def referenced_thresholds_exist(self) -> Self:
        referenced = (
            [
                threshold
                for score_steps in (*self.scores.values(), self.irr_penalty)
                for threshold, _points in score_steps.steps
            ]
            + [
                threshold
                for rule in self.recommendations
                for threshold in (rule.above, rule.below)
                if threshold is not None
            ]
            + [target.threshold for target in self.targets.values()]
        )
        unknown = sorted({name for name in referenced if name not in self.thresholds})
        if unknown:
            raise ValueError(f"пороги {unknown} не описаны в thresholds")
        return self

    @model_validator(mode="after")
    def every_kpi_has_target_and_scores(self) -> Self:
        missing_targets = [name for name in KPI_NAMES if name not in self.targets]
        if missing_targets:
            raise ValueError(f"targets: нет целей для {missing_targets}")
        if sorted(self.scores) != sorted(SCORED_KPI_NAMES):
            raise ValueError(
                f"scores: нужны ровно {list(SCORED_KPI_NAMES)}, есть {list(self.scores)}"
            )
        return self

    @model_validator(mode="after")
    def score_steps_are_monotonic(self) -> Self:
        # Ступени проверяются сверху вниз до первой выполненной (docs/kpi-definitions.md,
        # «Баллы»): при перепутанном порядке KPI молча получает не свою ступень.
        named_steps: list[tuple[str, ScoreSteps]] = [
            *self.scores.items(),
            ("irr_penalty", self.irr_penalty),
        ]
        for name, score_steps in named_steps:
            values = [self.thresholds[threshold] for threshold, _points in score_steps.steps]
            expected = sorted(values, reverse=score_steps.direction == "higher")
            points = [points for _threshold, points in score_steps.steps] + [score_steps.otherwise]
            if values != expected or len(set(values)) != len(values):
                raise ValueError(
                    f"{name}: пороги ступеней не монотонны для {score_steps.direction}"
                )
            if points != sorted(points, reverse=True):
                raise ValueError(f"{name}: баллы ступеней должны убывать сверху вниз")
        return self

    @model_validator(mode="after")
    def target_direction_matches_scores(self) -> Self:
        for name, target in self.targets.items():
            score_steps = self.irr_penalty if name == "irr" else self.scores.get(name)
            if score_steps is not None and score_steps.direction != target.direction:
                raise ValueError(f"targets.{name}: направление расходится со scores")
        return self

    def target_value(self, kpi_name: KpiName) -> float:
        return self.thresholds[self.targets[kpi_name].threshold]

    @model_validator(mode="after")
    def levels_descend_to_zero(self) -> Self:
        bounds = [level.min_spi for level in self.levels]
        if not bounds or bounds != sorted(bounds, reverse=True) or bounds[-1] != 0:
            raise ValueError("levels: min_spi по убыванию, последний уровень с min_spi 0")
        return self


class AppConfig(StrictConfigModel):
    status_mapping: StatusMapping
    modules: ModuleRegistry
    managers: ManagerRoster
    kpi: KpiSettings

    @model_validator(mode="after")
    def modules_needing_calibrated_kpi_stay_disabled(self) -> Self:
        # Баллы и SPI предварительные (ADR-002): модуль на них не включается ни из YAML,
        # ни через /settings, пока kpi.yaml не переведён в calibrated отдельным ADR.
        blocked = sorted(
            module_id
            for module_id, module in self.modules.all_modules.items()
            if module.enabled
            and module.requires_kpi_status is not None
            and self.kpi.status != module.requires_kpi_status
        )
        if blocked:
            raise ValueError(
                f"модули {blocked} требуют kpi.yaml status: calibrated, сейчас {self.kpi.status}"
            )
        return self

    @model_validator(mode="after")
    def manager_showrooms_are_known(self) -> Self:
        known = set(self.status_mapping.showrooms)
        for manager in self.managers.managers:
            if manager.showroom is not None and manager.showroom not in known:
                raise ValueError(
                    f"консультант {manager.id}: шоурум {manager.showroom!r} не из списка showrooms"
                )
        return self


def read_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as file:
        return yaml.safe_load(file)


def load_app_config(config_dir: Path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "status_mapping": read_yaml(config_dir / "status-mapping.yaml"),
            "modules": read_yaml(config_dir / "modules.yaml"),
            "managers": read_yaml(config_dir / "managers.yaml"),
            "kpi": read_yaml(config_dir / "kpi.yaml"),
        }
    )
