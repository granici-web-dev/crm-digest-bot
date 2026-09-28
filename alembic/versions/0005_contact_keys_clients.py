import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lead_snapshots", sa.Column("contact_phone_key", sa.Text(), nullable=True))
    op.add_column("lead_snapshots", sa.Column("contact_email_key", sa.Text(), nullable=True))

    op.create_table(
        "client_snapshots",
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("client_id", sa.Integer(), nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("showroom", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=True),
        sa.Column("source_name", sa.Text(), nullable=True),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint(
            "tenant_id", "snapshot_date", "client_id", name="pk_client_snapshots"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_client_snapshots_tenant_id_tenants"
        ),
    )

    op.add_column("snapshot_runs", sa.Column("clients_status", sa.Text(), nullable=True))
    op.add_column("snapshot_runs", sa.Column("clients_api_total", sa.Integer(), nullable=True))
    op.add_column("snapshot_runs", sa.Column("clients_written", sa.Integer(), nullable=True))
    op.add_column("snapshot_runs", sa.Column("clients_skipped", sa.Integer(), nullable=True))
    op.add_column(
        "snapshot_runs",
        sa.Column("clients_unknown_keys", postgresql.ARRAY(sa.Text()), nullable=True),
    )
    op.add_column("snapshot_runs", sa.Column("clients_error", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_snapshot_runs_clients_status",
        "snapshot_runs",
        "clients_status IN ('success', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_snapshot_runs_clients_status", "snapshot_runs", type_="check")
    for column in (
        "clients_error",
        "clients_unknown_keys",
        "clients_skipped",
        "clients_written",
        "clients_api_total",
        "clients_status",
    ):
        op.drop_column("snapshot_runs", column)
    op.drop_table("client_snapshots")
    op.drop_column("lead_snapshots", "contact_email_key")
    op.drop_column("lead_snapshots", "contact_phone_key")
