from dataclasses import dataclass
from typing import Any

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    JsonValue,
    StrictBool,
    StrictInt,
    TypeAdapter,
    ValidationError,
    model_validator,
)


class MefiModel(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)


class MefiNamedRef(MefiModel):
    id: StrictInt
    name: str


class MefiCustomField(MefiModel):
    field_id: StrictInt
    name: str
    value: JsonValue = None


@dataclass(frozen=True)
class InvalidShapeField:
    field_id: int | None
    name: str


LENIENT_FIELD_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    "is_duplicate": TypeAdapter(StrictBool),
    "status": TypeAdapter(MefiNamedRef),
    "source": TypeAdapter(MefiNamedRef),
    "assigned_to": TypeAdapter(MefiNamedRef),
    "status_changed_at": TypeAdapter(AwareDatetime),
    "last_contact_at": TypeAdapter(AwareDatetime),
    "converted_at": TypeAdapter(AwareDatetime),
}
CUSTOM_FIELD_ADAPTER = TypeAdapter(MefiCustomField)


def valid_custom_fields(
    raw_custom_fields: object, invalid_fields: list[InvalidShapeField]
) -> list[object]:
    if raw_custom_fields is None:
        return []
    if not isinstance(raw_custom_fields, list):
        invalid_fields.append(InvalidShapeField(None, "custom_fields"))
        return []
    valid: list[object] = []
    for element in raw_custom_fields:
        try:
            CUSTOM_FIELD_ADAPTER.validate_python(element)
        except ValidationError:
            field_id = element.get("field_id") if isinstance(element, dict) else None
            invalid_fields.append(
                InvalidShapeField(field_id if type(field_id) is int else None, "custom_fields")
            )
            continue
        valid.append(element)
    return valid


class MefiLead(MefiModel):
    id: StrictInt
    created_at: AwareDatetime
    is_duplicate: StrictBool | None = None
    status: MefiNamedRef | None = None
    source: MefiNamedRef | None = None
    assigned_to: MefiNamedRef | None = None
    status_changed_at: AwareDatetime | None = None
    last_contact_at: AwareDatetime | None = None
    converted_at: AwareDatetime | None = None
    custom_fields: list[MefiCustomField] = []
    invalid_shape_fields: list[InvalidShapeField] = []

    # Строго проверяются только id и created_at (PRINCIPLES.md, «Ошибки и валидация»):
    # из-за битого вторичного поля лид не выпадает из снапшота, поле становится null.
    @model_validator(mode="before")
    @classmethod
    def null_invalid_secondary_fields(cls, raw: Any) -> Any:
        if not isinstance(raw, dict):
            return raw
        invalid_fields: list[InvalidShapeField] = []
        lenient = dict(raw)
        for key, adapter in LENIENT_FIELD_ADAPTERS.items():
            value = lenient.get(key)
            if value is None:
                continue
            try:
                adapter.validate_python(value)
            except ValidationError:
                invalid_fields.append(InvalidShapeField(None, key))
                lenient[key] = None
        lenient["custom_fields"] = valid_custom_fields(lenient.get("custom_fields"), invalid_fields)
        lenient["invalid_shape_fields"] = invalid_fields
        return lenient


class MefiSearchMeta(MefiModel):
    page: StrictInt
    per_page: StrictInt
    total: StrictInt
    total_pages: StrictInt


class MefiSearchPage(MefiModel):
    success: bool
    data: list[dict[str, Any]]
    meta: MefiSearchMeta
