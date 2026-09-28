import copy
import logging
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
from pydantic import JsonValue, SecretStr, ValidationError
from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from digest.config import (
    UNMAPPED,
    ClientSettings,
    CompletenessThresholds,
    CustomFieldRef,
    LeadCategory,
    OfertatField,
    StatusMapping,
)
from digest.contact_keys import email_contact_key, phone_contact_key
from digest.db.schema import client_snapshots, lead_snapshots, snapshot_runs
from digest.mefi.client import (
    MefiClient,
    MefiClientsDump,
    MefiLeadsDump,
    MefiRateLimitExceeded,
    MefiSearchUnsuccessful,
)
from digest.mefi.models import MefiClientRecord, MefiCustomField, MefiLead

logger = logging.getLogger(__name__)

BUCHAREST = ZoneInfo("Europe/Bucharest")

# preview: снапшот, снятый до конца ежедневного окна. Отчёты и чат читают только success.
SnapshotRunStatus = Literal["success", "preview"]


@dataclass(frozen=True)
class SkippedLead:
    lead_id: int | None
    reason: str


@dataclass(frozen=True)
class CustomFieldProblem:
    field_id: int | None
    expected_name: str
    problem: str
    actual: str | None


@dataclass(frozen=True)
class SnapshotCounters:
    api_total: int
    leads_written: int
    unmapped_count: int
    skipped_count: int
    is_duplicate_missing: int
    skipped_leads: list[dict[str, Any]]
    rate_limited_count: int
    previous_snapshot_date: date | None
    missing_since_previous: int | None
    new_unmapped_lead_ids: list[int]
    won_converted_mismatch_ids: list[int]
    custom_field_mismatches: list[dict[str, Any]]


class SnapshotIncomplete(Exception):
    def __init__(self, reason: str, counters: SnapshotCounters) -> None:
        super().__init__(reason)
        self.counters = counters


@dataclass(frozen=True)
class ClientsCounters:
    clients_api_total: int
    clients_written: int
    clients_skipped: int
    clients_unknown_keys: list[str]


class ClientsSnapshotIncomplete(Exception):
    def __init__(self, reason: str, counters: ClientsCounters) -> None:
        super().__init__(reason)
        self.counters = counters


@dataclass(frozen=True)
class SnapshotOutcome:
    run_id: int
    status: SnapshotRunStatus
    # Текст для служебного бота: сбой клиентов, незнакомые ключи, битый шоурум клиента.
    clients_alert: str | None


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


def describe_error(error: BaseException) -> str:
    # str() у ValidationError и ошибок SQLAlchemy содержит тело ответа или строки raw,
    # поэтому наружу только тип и поля, в которых нет данных клиентов.
    error_type = type(error).__name__
    if isinstance(error, ValidationError):
        return f"{error_type}: {validation_reason(error)}"
    if isinstance(error, httpx.HTTPStatusError):
        return f"{error_type}: HTTP {error.response.status_code}"
    if isinstance(
        error,
        MefiRateLimitExceeded
        | MefiSearchUnsuccessful
        | SnapshotIncomplete
        | ClientsSnapshotIncomplete,
    ):
        return f"{error_type}: {error}"
    return error_type


def counters_known_at_failure(error: BaseException) -> dict[str, Any]:
    if isinstance(error, SnapshotIncomplete):
        return asdict(error.counters)
    if isinstance(error, MefiRateLimitExceeded):
        return {"rate_limited_count": error.rate_limited_count}
    return {}


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
    parsed_lead: ParsedLead,
    tenant_id: str,
    snapshot_date: date,
    status_mapping: StatusMapping,
    contact_secret: bytes,
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
        # Сами phone и email вырезаны raw_strip: для правил визита d1 хватает равенства ключей.
        "contact_phone_key": phone_contact_key(parsed_lead.raw.get("phone"), contact_secret),
        "contact_email_key": email_contact_key(parsed_lead.raw.get("email"), contact_secret),
    }
    problems = [
        problem
        for problem in (showroom_problem, ofertat_problem, data_revenire_problem)
        if problem is not None
    ]
    problems.extend(
        CustomFieldProblem(field.field_id, field.name, "invalid_shape", None)
        for field in lead.invalid_shape_fields
    )
    problems.extend(
        CustomFieldProblem(None, key, "unknown_raw_key", None)
        for key in sorted(parsed_lead.raw.keys() - status_mapping.raw_known_keys)
    )
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
    contact_hash_key: SecretStr,
) -> SnapshotCounters:
    parsed_leads, skipped_leads = parse_leads(dump.leads)
    contact_secret = contact_hash_key.get_secret_value().encode()
    rows: list[dict[str, Any]] = []
    problems_by_lead: list[tuple[int, CustomFieldProblem]] = []
    for parsed_lead in parsed_leads:
        row, problems = lead_to_snapshot_row(
            parsed_lead, tenant_id, snapshot_date, status_mapping, contact_secret
        )
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
    # Лид, пропущенный сегодня из-за битой формы, не исчез из mefi: он уже в skipped_leads.
    skipped_lead_ids = {skipped.lead_id for skipped in skipped_leads}

    if rows:
        await connection.execute(insert(lead_snapshots), rows)

    return SnapshotCounters(
        api_total=dump.api_total,
        leads_written=len(rows),
        unmapped_count=len(unmapped_lead_ids),
        skipped_count=len(skipped_leads),
        is_duplicate_missing=sum(1 for row in rows if row["is_duplicate"] is None),
        skipped_leads=[asdict(skipped) for skipped in skipped_leads],
        rate_limited_count=dump.rate_limited_count,
        previous_snapshot_date=previous_date,
        missing_since_previous=(
            len(previous_categories.keys() - current_lead_ids - skipped_lead_ids)
            if previous_date
            else None
        ),
        new_unmapped_lead_ids=sorted(unmapped_lead_ids - previously_unmapped_ids),
        won_converted_mismatch_ids=sorted(
            row["lead_id"]
            for row in rows
            if won_disagrees_with_converted_at(row["category"], row["converted_at"])
        ),
        custom_field_mismatches=summarize_custom_field_problems(problems_by_lead),
    )


def completeness_failure(
    counters: SnapshotCounters, thresholds: CompletenessThresholds
) -> str | None:
    api_total = counters.api_total
    received = counters.leads_written + counters.skipped_count
    if api_total > 0 and counters.leads_written == 0:
        return f"записано 0 лидов из {api_total}"
    missing_allowed = max(thresholds.max_missing_leads, thresholds.max_missing_share * api_total)
    if api_total - received > missing_allowed:
        return f"получено {received} лидов из {api_total}"
    skipped_allowed = max(thresholds.max_skipped_leads, thresholds.max_skipped_share * api_total)
    if counters.skipped_count > skipped_allowed:
        return f"пропущено {counters.skipped_count} битых лидов из {api_total}"
    return None


def parse_clients(
    raw_clients: list[dict[str, Any]],
) -> tuple[list[tuple[MefiClientRecord, dict[str, Any]]], int]:
    parsed: list[tuple[MefiClientRecord, dict[str, Any]]] = []
    skipped_count = 0
    for raw in raw_clients:
        try:
            parsed.append((MefiClientRecord.model_validate(raw), raw))
        except ValidationError:
            skipped_count += 1
    return parsed, skipped_count


def client_to_snapshot_row(
    client: MefiClientRecord,
    raw: dict[str, Any],
    tenant_id: str,
    snapshot_date: date,
    client_settings: ClientSettings,
) -> tuple[dict[str, Any], CustomFieldProblem | None]:
    fields_by_id = {field.field_id: field for field in client.custom_fields}
    showroom, showroom_problem = showroom_from(fields_by_id, client_settings.showroom)
    known_raw = {key: value for key, value in raw.items() if key in client_settings.raw_known_keys}
    row = {
        "tenant_id": tenant_id,
        "snapshot_date": snapshot_date,
        "client_id": client.id,
        "created_at": client.created_at,
        "showroom": showroom,
        "state": client.state,
        "source_name": client.source.name if client.source else None,
        "raw": strip_contacts(known_raw, client_settings.raw_strip),
    }
    return row, showroom_problem


async def write_client_snapshot(
    connection: AsyncConnection,
    dump: MefiClientsDump,
    tenant_id: str,
    snapshot_date: date,
    client_settings: ClientSettings,
) -> tuple[ClientsCounters, list[int]]:
    parsed_clients, skipped_count = parse_clients(dump.clients)
    rows: list[dict[str, Any]] = []
    showroom_problem_client_ids: list[int] = []
    unknown_keys: set[str] = set()
    for client, raw in parsed_clients:
        row, showroom_problem = client_to_snapshot_row(
            client, raw, tenant_id, snapshot_date, client_settings
        )
        rows.append(row)
        if showroom_problem is not None:
            showroom_problem_client_ids.append(client.id)
        unknown_keys |= raw.keys() - client_settings.raw_known_keys
    if rows:
        await connection.execute(insert(client_snapshots), rows)
    counters = ClientsCounters(
        clients_api_total=dump.api_total,
        clients_written=len(rows),
        clients_skipped=skipped_count,
        clients_unknown_keys=sorted(unknown_keys),
    )
    return counters, sorted(showroom_problem_client_ids)


def clients_completeness_failure(
    counters: ClientsCounters, thresholds: CompletenessThresholds
) -> str | None:
    # Те же пороги, что у лидов: недополученный клиент это недосчитанный контракт.
    api_total = counters.clients_api_total
    received = counters.clients_written + counters.clients_skipped
    if api_total > 0 and counters.clients_written == 0:
        return f"записано 0 клиентов из {api_total}"
    missing_allowed = max(thresholds.max_missing_leads, thresholds.max_missing_share * api_total)
    if api_total - received > missing_allowed:
        return f"получено {received} клиентов из {api_total}"
    skipped_allowed = max(thresholds.max_skipped_leads, thresholds.max_skipped_share * api_total)
    if counters.clients_skipped > skipped_allowed:
        return f"пропущено {counters.clients_skipped} битых клиентов из {api_total}"
    return None


def clients_alert_text(
    snapshot_date: date,
    error: str | None,
    unknown_keys: list[str],
    showroom_problem_client_ids: list[int],
) -> str | None:
    lines = []
    if error is not None:
        lines.append(
            f"Снапшот клиентов mefi за {snapshot_date:%d.%m.%Y} не удался: {error}. "
            "Contract Cantitate в d1 будет «—»."
        )
    if unknown_keys:
        lines.append(
            f"Клиенты mefi: незнакомые ключи {', '.join(unknown_keys)} не записаны в raw. "
            "Проверить, нет ли в них контактов, и дописать в clients.raw_known_keys "
            "(и в clients.raw_strip, если это персональные данные)."
        )
    if showroom_problem_client_ids:
        lines.append(
            f"Клиенты mefi без читаемого Showroom: {len(showroom_problem_client_ids)}, "
            f"id {', '.join(map(str, showroom_problem_client_ids[:10]))}. "
            "В d1 они в строке «Fără showroom»."
        )
    return "\n".join(lines) or None


def snapshot_run_lock(tenant_id: str, snapshot_date: date) -> Any:
    return select(
        func.pg_advisory_xact_lock(
            func.hashtextextended(f"snapshot_run:{tenant_id}:{snapshot_date}", 0)
        )
    )


async def snapshot_clients(
    engine: AsyncEngine,
    clients_client: MefiClient,
    client_settings: ClientSettings,
    thresholds: CompletenessThresholds,
    tenant_id: str,
    snapshot_date: date,
    run_id: int,
) -> str | None:
    this_run = snapshot_runs.c.id == run_id
    try:
        # День в запас: в какой таймзоне mefi режет дни фильтра, неизвестно.
        dump = await clients_client.search_all_clients(snapshot_date + timedelta(days=1))
        async with engine.begin() as connection:
            # Два параллельных повтора за дату не должны писать клиентов дважды.
            await connection.execute(snapshot_run_lock(tenant_id, snapshot_date))
            clients_status = await connection.scalar(
                select(snapshot_runs.c.clients_status).where(this_run)
            )
            if clients_status == "success":
                return None
            counters, showroom_problem_client_ids = await write_client_snapshot(
                connection, dump, tenant_id, snapshot_date, client_settings
            )
            failure = clients_completeness_failure(counters, thresholds)
            if failure is not None:
                raise ClientsSnapshotIncomplete(failure, counters)
            await connection.execute(
                update(snapshot_runs)
                .where(this_run)
                .values(clients_status="success", clients_error=None, **asdict(counters))
            )
    # BaseException: отмена задачи тоже должна оставить след, но дальше её пробрасываем.
    except BaseException as error:
        known_counters = (
            asdict(error.counters) if isinstance(error, ClientsSnapshotIncomplete) else {}
        )
        async with engine.begin() as connection:
            await connection.execute(
                update(snapshot_runs)
                .where(
                    this_run,
                    or_(
                        snapshot_runs.c.clients_status.is_(None),
                        snapshot_runs.c.clients_status != "success",
                    ),
                )
                .values(
                    clients_status="failed", clients_error=describe_error(error), **known_counters
                )
            )
        logger.error(
            "clients snapshot failed", extra={"run_id": run_id, "error": describe_error(error)}
        )
        if not isinstance(error, Exception):
            raise
        return clients_alert_text(snapshot_date, describe_error(error), [], [])
    logger.info(
        "clients snapshot done",
        extra={
            "run_id": run_id,
            "clients_api_total": counters.clients_api_total,
            "clients_written": counters.clients_written,
            "clients_skipped": counters.clients_skipped,
        },
    )
    return clients_alert_text(
        snapshot_date, None, counters.clients_unknown_keys, showroom_problem_client_ids
    )


@dataclass(frozen=True)
class SnapshotSources:
    leads_client: MefiClient
    clients_client: MefiClient
    contact_hash_key: SecretStr


async def run_daily_snapshot(
    engine: AsyncEngine,
    sources: SnapshotSources,
    status_mapping: StatusMapping,
    tenant_id: str,
    now: datetime,
) -> SnapshotOutcome:
    local_now = now.astimezone(BUCHAREST)
    snapshot_date = local_now.date()
    # Снапшот до конца окна не видит его хвост: окно d1 закончилось бы на времени запуска.
    run_status: SnapshotRunStatus = (
        "success" if local_now.time() >= status_mapping.time.daily_window_end else "preview"
    )
    this_date_runs = (snapshot_runs.c.tenant_id == tenant_id) & (
        snapshot_runs.c.snapshot_date == snapshot_date
    )
    async with engine.begin() as connection:
        # Без блокировки две попытки за одну дату (повтор в 19:10 поверх медленной 19:00,
        # ручной запуск) получат одинаковый attempt и будут гасить running-строки друг друга.
        await connection.execute(snapshot_run_lock(tenant_id, snapshot_date))
        success_run = (
            await connection.execute(
                select(snapshot_runs.c.id, snapshot_runs.c.clients_status).where(
                    this_date_runs, snapshot_runs.c.status == "success"
                )
            )
        ).first()
        if success_run is None:
            run_id = await start_snapshot_run(connection, tenant_id, snapshot_date)
    if success_run is not None:
        logger.info(
            "snapshot already done",
            extra={"snapshot_date": snapshot_date.isoformat(), "run_id": success_run.id},
        )
        if success_run.clients_status == "success":
            return SnapshotOutcome(success_run.id, "success", None)
        # Лиды уже есть, клиенты в прошлой попытке упали: повтор догружает только клиентов.
        return SnapshotOutcome(
            success_run.id,
            "success",
            await snapshot_clients(
                engine,
                sources.clients_client,
                status_mapping.clients,
                status_mapping.snapshot.completeness,
                tenant_id,
                snapshot_date,
                success_run.id,
            ),
        )

    await snapshot_leads(
        engine, sources, status_mapping, tenant_id, snapshot_date, run_id, run_status
    )
    # Сбой клиентов не валит снапшот лидов (инвариант 6): лиды уже закоммичены.
    return SnapshotOutcome(
        run_id,
        run_status,
        await snapshot_clients(
            engine,
            sources.clients_client,
            status_mapping.clients,
            status_mapping.snapshot.completeness,
            tenant_id,
            snapshot_date,
            run_id,
        ),
    )


async def start_snapshot_run(
    connection: AsyncConnection, tenant_id: str, snapshot_date: date
) -> int:
    this_date_runs = (snapshot_runs.c.tenant_id == tenant_id) & (
        snapshot_runs.c.snapshot_date == snapshot_date
    )
    # running от убитого процесса иначе висел бы вечно.
    await connection.execute(
        update(snapshot_runs)
        .where(this_date_runs, snapshot_runs.c.status == "running")
        .values(status="failed", finished_at=func.clock_timestamp(), error="superseded")
    )
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
    return run_id


async def supersede_previews(
    connection: AsyncConnection, tenant_id: str, snapshot_date: date
) -> None:
    # Строки снапшотов ключуются датой, а не прогоном: превью уходит целиком в транзакции
    # нового прогона и возвращается, если тот откатится. success за дату здесь не бывает:
    # run_daily_snapshot начинает прогон только без него, а два success запрещает индекс.
    for snapshot_table in (lead_snapshots, client_snapshots):
        await connection.execute(
            delete(snapshot_table).where(
                snapshot_table.c.tenant_id == tenant_id,
                snapshot_table.c.snapshot_date == snapshot_date,
            )
        )
    await connection.execute(
        update(snapshot_runs)
        .where(
            snapshot_runs.c.tenant_id == tenant_id,
            snapshot_runs.c.snapshot_date == snapshot_date,
            snapshot_runs.c.status == "preview",
        )
        .values(status="superseded")
    )


async def snapshot_leads(
    engine: AsyncEngine,
    sources: SnapshotSources,
    status_mapping: StatusMapping,
    tenant_id: str,
    snapshot_date: date,
    run_id: int,
    run_status: SnapshotRunStatus,
) -> None:
    started_at = time.monotonic()
    this_run = snapshot_runs.c.id == run_id
    try:
        dump = await sources.leads_client.search_all_leads()
        async with engine.begin() as connection:
            await connection.execute(snapshot_run_lock(tenant_id, snapshot_date))
            await supersede_previews(connection, tenant_id, snapshot_date)
            counters = await write_snapshot(
                connection,
                dump,
                tenant_id,
                snapshot_date,
                status_mapping,
                sources.contact_hash_key,
            )
            failure = completeness_failure(counters, status_mapping.snapshot.completeness)
            if failure is not None:
                raise SnapshotIncomplete(failure, counters)
            await connection.execute(
                update(snapshot_runs)
                .where(this_run)
                .values(
                    status=run_status,
                    finished_at=func.clock_timestamp(),
                    error=None,
                    duration_ms=round((time.monotonic() - started_at) * 1000),
                    **asdict(counters),
                )
            )
    # BaseException: отмена задачи (CancelledError) тоже должна закрыть running-строку.
    except BaseException as error:
        async with engine.begin() as connection:
            await connection.execute(
                update(snapshot_runs)
                .where(this_run)
                .values(
                    status="failed",
                    finished_at=func.clock_timestamp(),
                    duration_ms=round((time.monotonic() - started_at) * 1000),
                    error=describe_error(error),
                    **counters_known_at_failure(error),
                )
            )
        logger.error("snapshot failed", extra={"run_id": run_id, "error": describe_error(error)})
        raise

    logger.info(
        "snapshot done",
        extra={
            "run_id": run_id,
            "snapshot_date": snapshot_date.isoformat(),
            "api_total": counters.api_total,
            "leads_written": counters.leads_written,
            "unmapped_count": counters.unmapped_count,
            "skipped_count": counters.skipped_count,
        },
    )
