from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, JsonValue, PrivateAttr, model_validator

SourceCode = Literal["A", "B", "C", "D", "E", "F", "G", "H"]


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
    stale_after_days: int


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


class StatusMapping(StrictConfigModel):
    categories: Categories
    custom_fields: CustomFields
    sources: dict[str, list[str]]
    showrooms: list[str]
    time: TimeSettings
    raw_strip: list[str]

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
        self._category_by_status = category_by_status
        return self

    @property
    def category_by_status(self) -> dict[str, LeadCategory]:
        return self._category_by_status


class DataSource(StrictConfigModel):
    title: str
    connected: bool


class ReportModule(StrictConfigModel):
    name: str
    sources: list[SourceCode]
    enabled: bool
    params: dict[str, JsonValue] = {}


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

    @model_validator(mode="after")
    def enabled_modules_have_connected_sources(self) -> Self:
        all_modules = {**self.daily, **self.weekly, **self.monthly, **self.yearly}
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
        return self


class Manager(StrictConfigModel):
    id: int
    name: str
    showroom: str | None
    active: bool


class ManagerRoster(StrictConfigModel):
    managers: list[Manager]

    @model_validator(mode="after")
    def manager_ids_are_unique(self) -> Self:
        ids = [manager.id for manager in self.managers]
        duplicates = sorted({manager_id for manager_id in ids if ids.count(manager_id) > 1})
        if duplicates:
            raise ValueError(f"id консультантов повторяются: {duplicates}")
        return self


class AppConfig(StrictConfigModel):
    status_mapping: StatusMapping
    modules: ModuleRegistry
    managers: ManagerRoster

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
        }
    )
