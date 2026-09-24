"""foundation: tenants, lead snapshots, snapshot runs, settings, schedules, report runs

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    tenants = op.create_table(
        "tenants",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
    )
    op.bulk_insert(tenants, [{"id": "sofabelle", "name": "Sofabelle"}])

    op.create_table(
        "lead_snapshots",
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("lead_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.Text(), nullable=False),
        sa.Column("loss_reason", sa.Text(), nullable=True),
        sa.Column("status_name", sa.Text(), nullable=True),
        sa.Column("source_name", sa.Text(), nullable=True),
        sa.Column("showroom", sa.Text(), nullable=True),
        sa.Column("ofertat", sa.Boolean(), nullable=True),
        sa.Column("data_revenire", sa.Date(), nullable=True),
        sa.Column("is_duplicate", sa.Boolean(), nullable=False),
        sa.Column("assigned_to_id", sa.Integer(), nullable=True),
        sa.Column("assigned_to_name", sa.Text(), nullable=True),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status_changed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_contact_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("converted_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "snapshot_date", "lead_id", name="pk_lead_snapshots"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_lead_snapshots_tenant_id_tenants"
        ),
        sa.CheckConstraint(
            "category IN ('WON', 'ACTIVE', 'ACTIVE_FOLLOWUP', 'LOST', 'PARTNERSHIP', 'UNMAPPED')",
            name="ck_lead_snapshots_category",
        ),
    )

    op.create_table(
        "snapshot_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("attempt", sa.SmallInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("api_total", sa.Integer(), nullable=True),
        sa.Column("leads_written", sa.Integer(), nullable=True),
        sa.Column("unmapped_count", sa.Integer(), nullable=True),
        sa.Column("skipped_count", sa.Integer(), nullable=True),
        sa.Column("missing_since_previous", sa.Integer(), nullable=True),
        sa.Column("rate_limited_count", sa.Integer(), nullable=True),
        sa.Column("skipped_leads", postgresql.JSONB(), nullable=True),
        sa.Column("previous_snapshot_date", sa.Date(), nullable=True),
        sa.Column("new_unmapped_lead_ids", postgresql.ARRAY(sa.Integer()), nullable=True),
        sa.Column("won_converted_mismatch_ids", postgresql.ARRAY(sa.Integer()), nullable=True),
        sa.Column("custom_field_mismatches", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_snapshot_runs"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_snapshot_runs_tenant_id_tenants"
        ),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed')", name="ck_snapshot_runs_status"
        ),
    )
    op.create_index(
        "uq_snapshot_runs_success_per_date",
        "snapshot_runs",
        ["tenant_id", "snapshot_date"],
        unique=True,
        postgresql_where=sa.text("status = 'success'"),
    )

    op.create_table(
        "settings",
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "key", name="pk_settings"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_settings_tenant_id_tenants"
        ),
    )

    op.create_table(
        "module_settings",
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("module_id", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "params", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("tenant_id", "module_id", name="pk_module_settings"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_module_settings_tenant_id_tenants"
        ),
    )

    op.create_table(
        "schedules",
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("report_level", sa.Text(), nullable=False),
        sa.Column("cron", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id", "report_level", name="pk_schedules"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_schedules_tenant_id_tenants"
        ),
    )

    op.create_table(
        "report_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("report_level", sa.Text(), nullable=False),
        sa.Column("period_start", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("period_end", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "started_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("message_ids", postgresql.JSONB(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_report_runs"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_report_runs_tenant_id_tenants"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "report_level",
            "period_start",
            "chat_id",
            name="uq_report_runs_tenant_id_report_level_period_start_chat_id",
        ),
    )


def downgrade() -> None:
    for table_name in (
        "report_runs",
        "schedules",
        "module_settings",
        "settings",
        "snapshot_runs",
        "lead_snapshots",
        "tenants",
    ):
        op.drop_table(table_name)
