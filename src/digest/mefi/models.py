from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, JsonValue, StrictBool, StrictInt


class MefiModel(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)


class MefiNamedRef(MefiModel):
    id: StrictInt
    name: str


class MefiCustomField(MefiModel):
    field_id: StrictInt
    name: str
    value: JsonValue = None


class MefiLead(MefiModel):
    id: StrictInt
    created_at: AwareDatetime
    is_duplicate: StrictBool
    status: MefiNamedRef | None = None
    source: MefiNamedRef | None = None
    assigned_to: MefiNamedRef | None = None
    status_changed_at: AwareDatetime | None = None
    last_contact_at: AwareDatetime | None = None
    converted_at: AwareDatetime | None = None
    custom_fields: list[MefiCustomField] = []


class MefiSearchMeta(MefiModel):
    page: StrictInt
    per_page: StrictInt
    total: StrictInt
    total_pages: StrictInt


class MefiSearchPage(MefiModel):
    success: bool
    data: list[dict[str, Any]]
    meta: MefiSearchMeta
