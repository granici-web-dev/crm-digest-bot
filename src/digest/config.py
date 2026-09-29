from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Any, Literal, Self, get_args
from zoneinfo import ZoneInfo

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    NonNegativeInt,
    PositiveFloat,
    PositiveInt,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)

SourceCode = Literal["A", "B", "C", "D", "E", "F", "G", "H", "I"]
Share = Annotated[float, Field(ge=0, le=1)]

# Ключи причин, на которые ссылается metrics/: контракт между status-mapping.yaml и кодом.
# Это наши имена категорий, а не статусы mefi (PRINCIPLES.md, «Комментарии и имена»).
LOSS_REASONS_USED_BY_METRICS = ("IRELEVANT", "NU_RASPUNS", "BUGET", "PRODUS_NEPOTRIVIT", "STAND_BY")

SNAPSHOT_RETRY_DELAY = timedelta(minutes=10)
# Снапшот идёт минуты (пауза 1.2 с на запрос). running моложе этого снимает другой процесс, второй
# прогон его не вытесняет. Меньше SNAPSHOT_RETRY_DELAY: повтор 19:10 вытесняет зависший 19:00.
SNAPSHOT_RUNNING_ALIVE_FOR = timedelta(minutes=8)
# Проверка «снапшот дня есть» после повтора, с запасом на прогон в несколько минут.
FINAL_SNAPSHOT_CHECK_DELAY = timedelta(minutes=40)
# Снапшот в конце окна, повтор через SNAPSHOT_RETRY_DELAY, прогон при паузе 1.2 с на запрос
# идёт минуты: daily раньше этой границы прочитал бы вчерашний снапшот.
DAILY_REPORT_EARLIEST_AFTER_WINDOW_END = timedelta(minutes=30)
# Длиннее кнопка «✅ w12 · label» обрезается на экране телефона; полный заголовок модуля
# остаётся в docs/report-menu.md.
MODULE_LABEL_MAX_LENGTH = 28
SettingsLevel = Literal["daily", "weekly", "monthly"]
SETTINGS_LEVELS: tuple[SettingsLevel, ...] = get_args(SettingsLevel)
# Время засева берётся из send_times, чтобы оно всегда было вариантом меню /settings.
DEFAULT_SEND_TIME_INDEX: dict[SettingsLevel, int] = {"daily": 0, "weekly": 1, "monthly": 1}


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
    label: str


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


UTM_CAMPANIE = "UTM_Campanie"


class CustomFields(StrictConfigModel):
    showroom: CustomFieldRef
    ofertat: OfertatField
    data_revenire: CustomFieldRef
    modalitate_contact: CustomFieldRef
    utm: list[CustomFieldRef]

    @model_validator(mode="after")
    def utm_has_campaign(self) -> Self:
        if not any(field.name == UTM_CAMPANIE for field in self.utm):
            raise ValueError(f"custom_fields.utm: нет поля {UTM_CAMPANIE}, m7 и w6 без него пусты")
        return self

    @property
    def utm_campanie(self) -> CustomFieldRef:
        return next(field for field in self.utm if field.name == UTM_CAMPANIE)


class RawCustomFields(StrictConfigModel):
    keep: frozenset[int]
    drop: list[CustomFieldRef]

    @model_validator(mode="after")
    def kept_and_dropped_are_disjoint(self) -> Self:
        both = sorted(self.keep & self.drop_field_ids)
        if both:
            raise ValueError(f"raw_custom_fields: field_id {both} и в keep, и в drop")
        return self

    @property
    def drop_field_ids(self) -> frozenset[int]:
        return frozenset(field.field_id for field in self.drop)

    @property
    def known_field_ids(self) -> frozenset[int]:
        return self.keep | self.drop_field_ids


class WorkingHours(StrictConfigModel):
    start: time
    end: time


class TimeSettings(StrictConfigModel):
    timezone: Literal["Europe/Bucharest"]
    daily_window_end: time
    missed_snapshot_check: time
    working_hours: WorkingHours

    def snapshot_retry_at(self, snapshot_date: date) -> datetime:
        window_end = datetime.combine(
            snapshot_date, self.daily_window_end, tzinfo=ZoneInfo(self.timezone)
        )
        return window_end + SNAPSHOT_RETRY_DELAY


class CompletenessThresholds(StrictConfigModel):
    max_missing_leads: int
    max_missing_share: float
    max_skipped_leads: int
    max_skipped_share: float


class SnapshotSettings(StrictConfigModel):
    completeness: CompletenessThresholds


class ClientSettings(StrictConfigModel):
    showroom: CustomFieldRef
    contracts_count_from: date
    raw_known_keys: frozenset[str]
    raw_known_nested_keys: dict[str, frozenset[str]]
    raw_strip: list[str]
    raw_custom_fields: RawCustomFields

    @model_validator(mode="after")
    def stripped_keys_are_known(self) -> Self:
        unknown = sorted({path.split(".")[0] for path in self.raw_strip} - self.raw_known_keys)
        if unknown:
            raise ValueError(f"clients.raw_strip {unknown} нет в clients.raw_known_keys")
        return self

    @model_validator(mode="after")
    def nested_keys_belong_to_known_keys(self) -> Self:
        unknown = sorted(self.raw_known_nested_keys.keys() - self.raw_known_keys)
        if unknown:
            raise ValueError(
                f"clients.raw_known_nested_keys {unknown} нет в clients.raw_known_keys"
            )
        return self


class SourceGroups(StrictConfigModel):
    showroom_visit: list[str]
    web: list[str]
    # Подмножество web для правила d5 «сайт молчит»: Messenger и Meta ADS сбой формы не покажут.
    site: list[str]
    phone: list[str]
    whatsapp: list[str]
    partner: list[str]
    other: list[str]
    repeat_client: list[str]

    @model_validator(mode="after")
    def row_groups_are_disjoint(self) -> Self:
        # Строки d1 взаимоисключающие только при непересекающихся группах: источник в двух
        # группах молча посчитался бы в двух строках (site это подмножество web, не строка).
        group_by_source: dict[str, str] = {}
        for group_name in (
            "showroom_visit",
            "web",
            "phone",
            "whatsapp",
            "partner",
            "other",
            "repeat_client",
        ):
            for source in getattr(self, group_name):
                if source in group_by_source:
                    raise ValueError(
                        f"источник {source!r} и в sources.{group_by_source[source]}, "
                        f"и в sources.{group_name}"
                    )
                group_by_source[source] = group_name
        return self

    @model_validator(mode="after")
    def site_sources_are_web(self) -> Self:
        outside_web = [source for source in self.site if source not in self.web]
        if outside_web:
            raise ValueError(f"sources.site {outside_web} не входят в sources.web")
        return self


class LeadLinkSettings(StrictConfigModel):
    path: str
    limit: PositiveInt

    @field_validator("path")
    @classmethod
    def path_has_one_lead_id_placeholder(cls, value: str) -> str:
        if not value.startswith("/") or value.count("{lead_id}") != 1:
            raise ValueError("lead_links.path: путь от корня с ровно одним {lead_id}")
        if value.replace("{lead_id}", "").count("{"):
            raise ValueError("lead_links.path: кроме {lead_id} подстановок нет")
        return value


class StatusMapping(StrictConfigModel):
    categories: Categories
    custom_fields: CustomFields
    sources: SourceGroups
    showrooms: list[str]
    tenant_display_name: str
    without_source_label: str
    hidden_campaign_label: str
    leads_created_from: date
    lead_links: LeadLinkSettings
    time: TimeSettings
    raw_strip: list[str]
    raw_known_keys: frozenset[str]
    raw_known_nested_keys: dict[str, frozenset[str]]
    raw_custom_fields: RawCustomFields
    clients: ClientSettings
    snapshot: SnapshotSettings

    _category_by_status: dict[str, LeadCategory] = PrivateAttr()

    @model_validator(mode="after")
    def utm_campanie_is_kept_in_raw(self) -> Self:
        field_id = self.custom_fields.utm_campanie.field_id
        if field_id not in self.raw_custom_fields.keep:
            raise ValueError(
                f"{UTM_CAMPANIE} (field_id {field_id}) нет в raw_custom_fields.keep: "
                "m7 и w6 читают кампанию из raw"
            )
        return self

    @model_validator(mode="after")
    def stripped_keys_are_known(self) -> Self:
        unknown = sorted({path.split(".")[0] for path in self.raw_strip} - self.raw_known_keys)
        if unknown:
            raise ValueError(f"raw_strip {unknown} нет в raw_known_keys")
        return self

    @model_validator(mode="after")
    def nested_keys_belong_to_known_keys(self) -> Self:
        unknown = sorted(self.raw_known_nested_keys.keys() - self.raw_known_keys)
        if unknown:
            raise ValueError(f"raw_known_nested_keys {unknown} нет в raw_known_keys")
        return self

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
    label: Annotated[str, Field(min_length=1, max_length=MODULE_LABEL_MAX_LENGTH)]
    sources: list[SourceCode]
    enabled: bool
    params: dict[str, JsonValue] = {}
    requires_kpi_status: Literal["calibrated"] | None = None


class UntouchedLeadsParams(StrictConfigModel):
    threshold_hours: PositiveInt
    lookback_days: PositiveInt
    touch_tolerance_seconds: PositiveInt


class OverdueRevenireParams(StrictConfigModel):
    missing_followup_date: bool
    missing_followup_min_age_hours: PositiveInt


class AnomalyParams(StrictConfigModel):
    site_zero_min_average: PositiveFloat
    site_average_days: PositiveInt
    irelevant_spike_min: PositiveInt


class ScrBySourceCampaignParams(StrictConfigModel):
    min_source_leads: PositiveInt
    min_campaign_leads: PositiveInt
    campaign_label_max_length: PositiveInt = 40


class IrrByCampaignParams(StrictConfigModel):
    min_source_leads: PositiveInt
    min_campaign_leads: PositiveInt
    top_rows: PositiveInt
    campaign_label_max_length: PositiveInt = 40


class ScrLevel(StrictConfigModel):
    threshold: str
    label: str


class ScrLevelsParams(StrictConfigModel):
    levels: list[ScrLevel] = Field(min_length=1)
    below_label: str


ChatToolName = Literal[
    "funnel",
    "manager_kpi",
    "compare_periods",
    "loss_reasons",
    "overdue_followups",
    "untouched_leads",
]


class ChatSettings(StrictConfigModel):
    enabled: bool
    # Описание инструмента для модели (RO): продуктовый текст, живёт в конфиге, не в коде.
    tools: Annotated[dict[ChatToolName, str], Field(min_length=1)]
    # Описание параметра по имени, для всех инструментов с этим параметром.
    parameters: dict[str, str]
    trigger: Annotated[list[Literal["mention", "reply"]], Field(min_length=1)]
    daily_question_limit: PositiveInt
    context_minutes: PositiveInt
    max_tool_calls: PositiveInt
    max_answer_tokens: PositiveInt
    specific_date_history_years: PositiveInt


class CatchUpDays(StrictConfigModel):
    daily: NonNegativeInt
    weekly: NonNegativeInt
    monthly: NonNegativeInt

    def for_level(self, level: SettingsLevel) -> int:
        return {"daily": self.daily, "weekly": self.weekly, "monthly": self.monthly}[level]


class ModuleRegistry(StrictConfigModel):
    sources: dict[SourceCode, DataSource]
    daily: dict[str, ReportModule]
    weekly: dict[str, ReportModule]
    monthly: dict[str, ReportModule]
    yearly: dict[str, ReportModule]
    chat: ChatSettings
    send_times: dict[SettingsLevel, Annotated[list[time], Field(min_length=1)]]
    catch_up_days: CatchUpDays

    @property
    def all_modules(self) -> dict[str, ReportModule]:
        return {**self.daily, **self.weekly, **self.monthly, **self.yearly}

    _untouched_leads_params: UntouchedLeadsParams = PrivateAttr()
    _overdue_revenire_params: OverdueRevenireParams = PrivateAttr()
    _anomaly_params: AnomalyParams = PrivateAttr()
    _scr_levels_params: ScrLevelsParams = PrivateAttr()
    _scr_by_source_campaign_params: ScrBySourceCampaignParams = PrivateAttr()
    _irr_by_campaign_params: IrrByCampaignParams = PrivateAttr()

    @property
    def untouched_leads_params(self) -> UntouchedLeadsParams:
        return self._untouched_leads_params

    @property
    def overdue_revenire_params(self) -> OverdueRevenireParams:
        return self._overdue_revenire_params

    @property
    def anomaly_params(self) -> AnomalyParams:
        return self._anomaly_params

    @property
    def scr_levels_params(self) -> ScrLevelsParams:
        return self._scr_levels_params

    @property
    def scr_by_source_campaign_params(self) -> ScrBySourceCampaignParams:
        return self._scr_by_source_campaign_params

    @property
    def irr_by_campaign_params(self) -> IrrByCampaignParams:
        return self._irr_by_campaign_params

    def module_named(self, name: str) -> tuple[str, ReportModule]:
        for module_id, module in self.all_modules.items():
            if module.name == name:
                return module_id, module
        raise ValueError(f"модуль {name} не найден в config/modules.yaml")

    def parsed_params[ParamsModel: StrictConfigModel](
        self, module_name: str, params_model: type[ParamsModel]
    ) -> ParamsModel:
        module_id, module = self.module_named(module_name)
        try:
            return params_model.model_validate(module.params)
        except ValidationError as error:
            raise ValueError(f"модуль {module_id}: неверные params: {error}") from error

    @model_validator(mode="after")
    def module_ids_are_unique(self) -> Self:
        module_count = sum(
            len(level) for level in (self.daily, self.weekly, self.monthly, self.yearly)
        )
        if len(self.all_modules) != module_count:
            raise ValueError("id модуля повторяется в разных отчётах")
        return self

    @model_validator(mode="after")
    def send_times_are_unique_per_level(self) -> Self:
        for level in SETTINGS_LEVELS:
            if level not in self.send_times:
                raise ValueError(f"send_times: нет вариантов для {level}")
            options = self.send_times[level]
            if len(set(options)) != len(options):
                raise ValueError(f"send_times.{level}: варианты повторяются")
            if len(options) <= DEFAULT_SEND_TIME_INDEX[level]:
                raise ValueError(
                    f"send_times.{level}: время по умолчанию это вариант "
                    f"№{DEFAULT_SEND_TIME_INDEX[level] + 1}, вариантов меньше"
                )
        return self

    def default_send_time(self, level: SettingsLevel) -> time:
        return self.send_times[level][DEFAULT_SEND_TIME_INDEX[level]]

    @model_validator(mode="after")
    def module_sources_are_declared(self) -> Self:
        for module_id, module in self.all_modules.items():
            undeclared = [code for code in module.sources if code not in self.sources]
            if undeclared:
                raise ValueError(f"модуль {module_id}: источники {undeclared} не описаны в sources")
        return self

    @model_validator(mode="after")
    def module_params_are_parsed(self) -> Self:
        # Один разбор при загрузке: опечатка в пороге роняет старт, а не отчёт в 19:30.
        self._untouched_leads_params = self.parsed_params("untouched_leads", UntouchedLeadsParams)
        self._overdue_revenire_params = self.parsed_params(
            "overdue_revenire", OverdueRevenireParams
        )
        self._anomaly_params = self.parsed_params("anomalies", AnomalyParams)
        self._scr_levels_params = self.parsed_params("scr_with_targets", ScrLevelsParams)
        self._scr_by_source_campaign_params = self.parsed_params(
            "scr_by_source_campaign", ScrBySourceCampaignParams
        )
        self._irr_by_campaign_params = self.parsed_params("irr_by_campaign", IrrByCampaignParams)
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


@dataclass(frozen=True)
class ModuleBlockers:
    disconnected_sources: tuple[SourceCode, ...]
    missing_kpi_status: KpiStatus | None


class AppConfig(StrictConfigModel):
    status_mapping: StatusMapping
    modules: ModuleRegistry
    managers: ManagerRoster
    kpi: KpiSettings

    def module_blockers(self, module_id: str) -> ModuleBlockers:
        module = self.modules.all_modules[module_id]
        required_kpi_status = module.requires_kpi_status
        return ModuleBlockers(
            disconnected_sources=tuple(
                code for code in module.sources if not self.modules.sources[code].connected
            ),
            missing_kpi_status=(
                required_kpi_status
                if required_kpi_status is not None and required_kpi_status != self.kpi.status
                else None
            ),
        )

    @model_validator(mode="after")
    def enabled_modules_are_available(self) -> Self:
        # Баллы и SPI предварительные (ADR-002): модуль на них не включается ни из YAML,
        # ни через /settings, пока kpi.yaml не переведён в calibrated отдельным ADR.
        for module_id, module in self.modules.all_modules.items():
            if not module.enabled:
                continue
            blockers = self.module_blockers(module_id)
            if blockers.disconnected_sources:
                raise ValueError(
                    f"модуль {module_id} включён, но источники "
                    f"{list(blockers.disconnected_sources)} не подключены"
                )
            if blockers.missing_kpi_status is not None:
                raise ValueError(
                    f"модуль {module_id} требует kpi.yaml status: {blockers.missing_kpi_status}, "
                    f"сейчас {self.kpi.status}"
                )
        return self

    @model_validator(mode="after")
    def daily_send_times_follow_snapshot(self) -> Self:
        window_end = self.status_mapping.time.daily_window_end
        earliest = (
            datetime.combine(date.min, window_end) + DAILY_REPORT_EARLIEST_AFTER_WINDOW_END
        ).time()
        too_early = [option for option in self.modules.send_times["daily"] if option < earliest]
        if too_early:
            raise ValueError(
                f"send_times.daily: {[f'{option:%H:%M}' for option in too_early]} "
                f"раньше {earliest:%H:%M}, снапшот ещё не готов"
            )
        return self

    @model_validator(mode="after")
    def scr_levels_are_descending_thresholds(self) -> Self:
        # Ступени m4 ссылаются на пороги kpi.yaml по имени: число живёт только в thresholds.
        levels = [level.threshold for level in self.modules.scr_levels_params.levels]
        unknown = [name for name in levels if name not in self.kpi.thresholds]
        if unknown:
            raise ValueError(f"модуль scr_with_targets: пороги {unknown} не найдены в kpi.yaml")
        values = [self.kpi.thresholds[name] for name in levels]
        if any(higher <= lower for higher, lower in pairwise(values)):
            raise ValueError(
                f"модуль scr_with_targets: levels {levels} должны строго убывать, сейчас {values}"
            )
        return self

    @model_validator(mode="after")
    def scr_level_labels_differ_from_spi_levels(self) -> Self:
        # Уровни SPI в отчётах запрещены до калибровки (ADR-002): ступень SCR в m4 не должна
        # читаться как уровень SPI.
        params = self.modules.scr_levels_params
        labels = [*(level.label for level in params.levels), params.below_label]
        spi_level_names = {level.name.casefold() for level in self.kpi.levels}
        clashing = [label for label in labels if label.casefold() in spi_level_names]
        if clashing:
            raise ValueError(
                f"модуль scr_with_targets: подписи {clashing} совпадают с уровнями SPI kpi.yaml"
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
