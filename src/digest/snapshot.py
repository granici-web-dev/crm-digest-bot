import copy
import logging
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import JsonValue, ValidationError
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from digest.config import UNMAPPED, CustomFieldRef, LeadCategory, OfertatField, StatusMapping
from digest.db.schema import lead_snapshots, snapshot_runs
from digest.mefi.client import MefiClient, MefiLeadsDump, MefiRateLimitExceeded
from digest.mefi.models import MefiCustomField, MefiLead

logger = logging.getLogger(__name__)

BUCHAREST = ZoneInfo("Europe/Bucharest")


@dataclass(frozen=True)
class SkippedLead:
    lead_id: int | None
    reason: str


@dataclass(frozen=True)
class CustomFieldProblem:
    field_id: int
    expected_name: str
    problem: str
    actual: str | None


@dataclass(frozen=True)
class ParsedLead:
    lead: MefiLead
    raw: dict[str, Any]


def categorize(status_name: str | None, status_mapping: StatusMapping) -> LeadCategory:
    if status_name is None:
        return UNMAPPED
    return status_mapping.category_by_status.get(status_name.strip(), UNMAPPED)


def validation_reason(error: ValidationError) -> str:
    # Без input: иначе в причину попадёт весь лид вместе с телефоном и e-mail.
    return "; ".join(
        f"{'.'.join(str(part) for part in detail['loc'])}: {detail['type']}"
        for detail in error.errors(include_input=False, include_url=False)
    )


def parse_leads(raw_leads: list[dict[str, Any]]) -> tuple[list[ParsedLead], list[SkippedLead]]:
    parsed: list[ParsedLead] = []
    skipped: list[SkippedLead] = []
    for raw in raw_leads:
        try:
            parsed.append(ParsedLead(MefiLead.model_validate(raw), raw))
        except ValidationError as error:
            raw_id = raw.get("id")
            skipped.append(
                SkippedLead(
                    lead_id=raw_id if isinstance(raw_id, int) else None,
                    reason=validation_reason(error),
                )
            )
    return parsed, skipped


def strip_contacts(raw: dict[str, Any], paths: list[str]) -> dict[str, Any]:
    stripped = copy.deepcopy(raw)
    for path in paths:
        *parent_keys, leaf_key = path.split(".")
        container: Any = stripped
        for key in parent_keys:
            container = container.get(key) if isinstance(container, dict) else None
        if isinstance(container, dict):
            container.pop(leaf_key, None)
    return stripped


def read_custom_field(
    fields_by_id: dict[int, MefiCustomField], reference: CustomFieldRef
) -> tuple[JsonValue, CustomFieldProblem | None]:
    field = fields_by_id.get(reference.field_id)
    if field is None:
        return None, CustomFieldProblem(reference.field_id, reference.name, "missing", None)
    # Чужое поле под нашим field_id дало бы неверную цифру, поэтому null, а не значение.
    if field.name != reference.name:
        return None, CustomFieldProblem(
            reference.field_id, reference.name, "name_mismatch", field.name
        )
    return field.value, None


def unexpected_value(reference: CustomFieldRef, value: JsonValue) -> CustomFieldProblem:
    return CustomFieldProblem(reference.field_id, reference.name, "unexpected_value", str(value))


def showroom_from(
    fields_by_id: dict[int, MefiCustomField], reference: CustomFieldRef
) -> tuple[str | None, CustomFieldProblem | None]:
    value, problem = read_custom_field(fields_by_id, reference)
    if value is None or isinstance(value, str):
        return value, problem
    return None, unexpected_value(reference, value)


def ofertat_from(
    fields_by_id: dict[int, MefiCustomField], reference: OfertatField
) -> tuple[bool | None, CustomFieldProblem | None]:
    value, problem = read_custom_field(fields_by_id, reference)
    if value is None:
        return None, problem
    if value == reference.ofertat_yes:
        return True, None
    if value == reference.ofertat_no:
        return False, None
    return None, unexpected_value(reference, value)


def data_revenire_from(
    fields_by_id: dict[int, MefiCustomField], reference: CustomFieldRef
) -> tuple[date | None, CustomFieldProblem | None]:
    value, problem = read_custom_field(fields_by_id, reference)
    if value is None:
        return None, problem
    if isinstance(value, str):
        try:
            return date.fromisoformat(value), None
        except ValueError:
            pass
    return None, unexpected_value(reference, value)


def lead_to_snapshot_row(
    parsed_lead: ParsedLead, tenant_id: str, snapshot_date: date, status_mapping: StatusMapping
) -> tuple[dict[str, Any], list[CustomFieldProblem]]:
    lead = parsed_lead.lead
    custom_fields = status_mapping.custom_fields
    fields_by_id = {field.field_id: field for field in lead.custom_fields}
    showroom, showroom_problem = showroom_from(fields_by_id, custom_fields.showroom)
    ofertat, ofertat_problem = ofertat_from(fields_by_id, custom_fields.ofertat)
    data_revenire, data_revenire_problem = data_revenire_from(
        fields_by_id, custom_fields.data_revenire
    )
    category = categorize(lead.status.name if lead.status else None, status_mapping)
    row = {
        "tenant_id": tenant_id,
        "snapshot_date": snapshot_date,
        "lead_id": lead.id,
        "category": category.category,
        "loss_reason": category.loss_reason,
        "status_name": lead.status.name if lead.status else None,
        "source_name": lead.source.name if lead.source else None,
        "showroom": showroom,
        "ofertat": ofertat,
        "data_revenire": data_revenire,
        "is_duplicate": lead.is_duplicate,
        "assigned_to_id": lead.assigned_to.id if lead.assigned_to else None,
        "assigned_to_name": lead.assigned_to.name if lead.assigned_to else None,
        "created_at": lead.created_at,
        "status_changed_at": lead.status_changed_at,
        "last_contact_at": lead.last_contact_at,
        "converted_at": lead.converted_at,
        "raw": strip_contacts(parsed_lead.raw, status_mapping.raw_strip),
    }
    problems = [
        problem
        for problem in (showroom_problem, ofertat_problem, data_revenire_problem)
        if problem is not None
    ]
    return row, problems


def summarize_custom_field_problems(
    problems_by_lead: list[tuple[int, CustomFieldProblem]],
) -> list[dict[str, Any]]:
    lead_ids_by_problem: defaultdict[CustomFieldProblem, list[int]] = defaultdict(list)
    for lead_id, problem in problems_by_lead:
        lead_ids_by_problem[problem].append(lead_id)
    return [
        {**asdict(problem), "lead_count": len(lead_ids), "lead_ids": lead_ids}
        for problem, lead_ids in lead_ids_by_problem.items()
    ]


def won_disagrees_with_converted_at(category: str, converted_at: datetime | None) -> bool:
    return (category == "WON") != (converted_at is not None)


async def previous_success_snapshot(
    connection: AsyncConnection, tenant_id: str, snapshot_date: date
) -> tuple[date | None, dict[int, str]]:
    previous_date = await connection.scalar(
        select(func.max(snapshot_runs.c.snapshot_date)).where(
            snapshot_runs.c.tenant_id == tenant_id,
            snapshot_runs.c.status == "success",
            snapshot_runs.c.snapshot_date < snapshot_date,
        )
    )
    if previous_date is None:
        return None, {}
    previous_rows = await connection.execute(
        select(lead_snapshots.c.lead_id, lead_snapshots.c.category).where(
            lead_snapshots.c.tenant_id == tenant_id,
            lead_snapshots.c.snapshot_date == previous_date,
        )
    )
    return previous_date, {lead_id: category for lead_id, category in previous_rows}


async def write_snapshot(
    connection: AsyncConnection,
    dump: MefiLeadsDump,
    tenant_id: str,
    snapshot_date: date,
    status_mapping: StatusMapping,
) -> dict[str, Any]:
    parsed_leads, skipped_leads = parse_leads(dump.leads)
    rows: list[dict[str, Any]] = []
    problems_by_lead: list[tuple[int, CustomFieldProblem]] = []
    for parsed_lead in parsed_leads:
        row, problems = lead_to_snapshot_row(parsed_lead, tenant_id, snapshot_date, status_mapping)
        rows.append(row)
        problems_by_lead.extend((parsed_lead.lead.id, problem) for problem in problems)

    unmapped_lead_ids = {row["lead_id"] for row in rows if row["category"] == UNMAPPED.category}
    previous_date, previous_categories = await previous_success_snapshot(
        connection, tenant_id, snapshot_date
    )
    previously_unmapped_ids = {
        lead_id
        for lead_id, category in previous_categories.items()
        if category == UNMAPPED.category
    }
    current_lead_ids = {row["lead_id"] for row in rows}

    if rows:
        await connection.execute(insert(lead_snapshots), rows)

    return {
        "api_total": dump.api_total,
        "leads_written": len(rows),
        "unmapped_count": len(unmapped_lead_ids),
        "skipped_count": len(skipped_leads),
        "skipped_leads": [asdict(skipped) for skipped in skipped_leads],
        "rate_limited_count": dump.rate_limited_count,
        "previous_snapshot_date": previous_date,
        "missing_since_previous": (
            len(previous_categories.keys() - current_lead_ids) if previous_date else None
        ),
        "new_unmapped_lead_ids": sorted(unmapped_lead_ids - previously_unmapped_ids),
        "won_converted_mismatch_ids": sorted(
            row["lead_id"]
            for row in rows
            if won_disagrees_with_converted_at(row["category"], row["converted_at"])
        ),
        "custom_field_mismatches": summarize_custom_field_problems(problems_by_lead),
    }


async def run_daily_snapshot(
    engine: AsyncEngine,
    mefi_client: MefiClient,
    status_mapping: StatusMapping,
    tenant_id: str,
    now: datetime,
) -> int:
    snapshot_date = now.astimezone(BUCHAREST).date()
    this_date_runs = (snapshot_runs.c.tenant_id == tenant_id) & (
        snapshot_runs.c.snapshot_date == snapshot_date
    )
    async with engine.begin() as connection:
        success_run_id = await connection.scalar(
            select(snapshot_runs.c.id).where(this_date_runs, snapshot_runs.c.status == "success")
        )
        if success_run_id is not None:
            logger.info(
                "snapshot already done",
                extra={"snapshot_date": snapshot_date.isoformat(), "run_id": success_run_id},
            )
            return int(success_run_id)
        previous_attempts = await connection.scalar(
            select(func.count()).select_from(snapshot_runs).where(this_date_runs)
        )
        inserted = await connection.execute(
            insert(snapshot_runs)
            .values(
                tenant_id=tenant_id,
                snapshot_date=snapshot_date,
                attempt=(previous_attempts or 0) + 1,
                status="running",
            )
            .returning(snapshot_runs.c.id)
        )
        run_id: int = inserted.scalar_one()

    started_at = time.monotonic()
    this_run = snapshot_runs.c.id == run_id
    try:
        dump = await mefi_client.search_all_leads()
        async with engine.begin() as connection:
            counters = await write_snapshot(
                connection, dump, tenant_id, snapshot_date, status_mapping
            )
            await connection.execute(
                update(snapshot_runs)
                .where(this_run)
                .values(
                    status="success",
                    finished_at=func.clock_timestamp(),
                    duration_ms=round((time.monotonic() - started_at) * 1000),
                    **counters,
                )
            )
    except Exception as error:
        async with engine.begin() as connection:
            await connection.execute(
                update(snapshot_runs)
                .where(this_run)
                .values(
                    status="failed",
                    finished_at=func.clock_timestamp(),
                    duration_ms=round((time.monotonic() - started_at) * 1000),
                    rate_limited_count=(
                        error.rate_limited_count
                        if isinstance(error, MefiRateLimitExceeded)
                        else None
                    ),
                    error=f"{type(error).__name__}: {error}",
                )
            )
        logger.exception("snapshot failed", extra={"run_id": run_id})
        raise

    logger.info(
        "snapshot done",
        extra={
            "run_id": run_id,
            "snapshot_date": snapshot_date.isoformat(),
            **{
                key: counters[key]
                for key in ("api_total", "leads_written", "unmapped_count", "skipped_count")
            },
        },
    )
    return run_id
