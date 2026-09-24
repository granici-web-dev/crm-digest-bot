from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    SmallInteger,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TIMESTAMP

metadata = MetaData(
    naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_N_name)s",
        "uq": "uq_%(table_name)s_%(column_0_N_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

LEAD_CATEGORIES = ("WON", "ACTIVE", "ACTIVE_FOLLOWUP", "LOST", "PARTNERSHIP", "UNMAPPED")
SNAPSHOT_RUN_STATUSES = ("running", "success", "failed")


def _tenant_id_column() -> Column[str]:
    return Column("tenant_id", Text, ForeignKey("tenants.id"), nullable=False)


def _timestamptz(name: str, *, nullable: bool = True) -> Column[datetime]:
    return Column(name, TIMESTAMP(timezone=True), nullable=nullable)


def _sql_in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


tenants = Table(
    "tenants",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
)

lead_snapshots = Table(
    "lead_snapshots",
    metadata,
    _tenant_id_column(),
    Column("snapshot_date", Date, nullable=False),
    Column("lead_id", Integer, nullable=False),
    Column("category", Text, nullable=False),
    Column("loss_reason", Text),
    Column("status_name", Text),
    Column("source_name", Text),
    Column("showroom", Text),
    Column("ofertat", Boolean),
    Column("data_revenire", Date),
    Column("is_duplicate", Boolean),
    Column("assigned_to_id", Integer),
    Column("assigned_to_name", Text),
    _timestamptz("created_at", nullable=False),
    _timestamptz("status_changed_at"),
    _timestamptz("last_contact_at"),
    _timestamptz("converted_at"),
    Column("raw", JSONB, nullable=False),
    PrimaryKeyConstraint("tenant_id", "snapshot_date", "lead_id"),
    CheckConstraint(f"category IN ({_sql_in_list(LEAD_CATEGORIES)})", name="category"),
)

snapshot_runs = Table(
    "snapshot_runs",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    _tenant_id_column(),
    Column("snapshot_date", Date, nullable=False),
    Column("attempt", SmallInteger, nullable=False),
    Column("status", Text, nullable=False),
    Column("started_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    _timestamptz("finished_at"),
    Column("duration_ms", Integer),
    Column("api_total", Integer),
    Column("leads_written", Integer),
    Column("unmapped_count", Integer),
    Column("skipped_count", Integer),
    Column("is_duplicate_missing", Integer),
    Column("missing_since_previous", Integer),
    Column("rate_limited_count", Integer),
    Column("skipped_leads", JSONB),
    Column("previous_snapshot_date", Date),
    Column("new_unmapped_lead_ids", ARRAY(Integer)),
    Column("won_converted_mismatch_ids", ARRAY(Integer)),
    Column("custom_field_mismatches", JSONB),
    Column("error", Text),
    CheckConstraint(f"status IN ({_sql_in_list(SNAPSHOT_RUN_STATUSES)})", name="status"),
    # Отчёты берут дату только при единственном success: второй success за дату невозможен.
    Index(
        "uq_snapshot_runs_success_per_date",
        "tenant_id",
        "snapshot_date",
        unique=True,
        postgresql_where=text("status = 'success'"),
    ),
)

settings = Table(
    "settings",
    metadata,
    _tenant_id_column(),
    Column("key", Text, nullable=False),
    Column("value", JSONB, nullable=False),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_by", BigInteger),
    PrimaryKeyConstraint("tenant_id", "key"),
)

module_settings = Table(
    "module_settings",
    metadata,
    _tenant_id_column(),
    Column("module_id", Text, nullable=False),
    Column("enabled", Boolean, nullable=False),
    Column("params", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_by", BigInteger),
    PrimaryKeyConstraint("tenant_id", "module_id"),
)

schedules = Table(
    "schedules",
    metadata,
    _tenant_id_column(),
    Column("report_level", Text, nullable=False),
    Column("cron", Text, nullable=False),
    Column("enabled", Boolean, nullable=False),
    PrimaryKeyConstraint("tenant_id", "report_level"),
)

report_runs = Table(
    "report_runs",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    _tenant_id_column(),
    Column("report_level", Text, nullable=False),
    _timestamptz("period_start", nullable=False),
    _timestamptz("period_end", nullable=False),
    Column("snapshot_date", Date),
    Column("chat_id", BigInteger, nullable=False),
    Column("status", Text, nullable=False),
    Column("started_at", TIMESTAMP(timezone=True), nullable=False, server_default=func.now()),
    _timestamptz("finished_at"),
    Column("error", Text),
    Column("message_ids", JSONB),
    UniqueConstraint("tenant_id", "report_level", "period_start", "chat_id"),
)
