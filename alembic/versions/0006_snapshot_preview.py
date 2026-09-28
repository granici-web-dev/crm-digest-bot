from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_snapshot_runs_status", "snapshot_runs", type_="check")
    op.create_check_constraint(
        "ck_snapshot_runs_status",
        "snapshot_runs",
        "status IN ('running', 'success', 'failed', 'preview', 'superseded')",
    )
    # Снапшот, начатый до конца окна своей даты, не видит хвост окна: это превью, а не
    # снапшот дня. 19:00 это time.daily_window_end на момент миграции; правило в run_daily_snapshot.
    op.execute(
        "UPDATE snapshot_runs SET status = 'preview' "
        "WHERE status = 'success' "
        "AND started_at < (snapshot_date + time '19:00') AT TIME ZONE 'Europe/Bucharest'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE snapshot_runs SET status = 'failed', error = 'preview before 0006 downgrade' "
        "WHERE status IN ('preview', 'superseded')"
    )
    op.drop_constraint("ck_snapshot_runs_status", "snapshot_runs", type_="check")
    op.create_check_constraint(
        "ck_snapshot_runs_status",
        "snapshot_runs",
        "status IN ('running', 'success', 'failed')",
    )
