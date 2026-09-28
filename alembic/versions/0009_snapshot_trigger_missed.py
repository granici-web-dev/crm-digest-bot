from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Кто снял снапшот, до 0009 не записывалось: старые строки считаются штатными.
    op.execute("ALTER TABLE snapshot_runs ADD COLUMN trigger text NOT NULL DEFAULT 'scheduled'")
    op.execute("ALTER TABLE snapshot_runs ALTER COLUMN trigger DROP DEFAULT")
    op.create_check_constraint(
        "ck_snapshot_runs_trigger",
        "snapshot_runs",
        "trigger IN ('scheduled', 'retry', 'catch_up', 'manual')",
    )
    op.drop_constraint("ck_snapshot_runs_status", "snapshot_runs", type_="check")
    op.create_check_constraint(
        "ck_snapshot_runs_status",
        "snapshot_runs",
        "status IN ('running', 'success', 'failed', 'preview', 'superseded', 'missed')",
    )
    op.alter_column("snapshot_runs", "attempt", nullable=True)
    # 54ceb33 писал пропущенную дату как failed с error = 'missed'.
    op.execute(
        "UPDATE snapshot_runs SET status = 'missed', attempt = NULL, error = NULL "
        "WHERE status = 'failed' AND error = 'missed'"
    )
    op.create_check_constraint(
        "ck_snapshot_runs_attempt_unless_missed",
        "snapshot_runs",
        "(status = 'missed') = (attempt IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_snapshot_runs_attempt_unless_missed", "snapshot_runs", type_="check")
    op.execute(
        "UPDATE snapshot_runs SET status = 'failed', attempt = 0, error = 'missed' "
        "WHERE status = 'missed'"
    )
    op.alter_column("snapshot_runs", "attempt", nullable=False)
    op.drop_constraint("ck_snapshot_runs_status", "snapshot_runs", type_="check")
    op.create_check_constraint(
        "ck_snapshot_runs_status",
        "snapshot_runs",
        "status IN ('running', 'success', 'failed', 'preview', 'superseded')",
    )
    op.drop_constraint("ck_snapshot_runs_trigger", "snapshot_runs", type_="check")
    op.drop_column("snapshot_runs", "trigger")
