from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Старые строки не заполняются: d3 и чат читают только последний снапшот, а до 0010 поле
    # читалось у всех лидов (замер 28.09: поле есть у 3056 из 3056, значение null или дата).
    op.execute("ALTER TABLE lead_snapshots ADD COLUMN data_revenire_problem text")
    op.create_check_constraint(
        "ck_lead_snapshots_data_revenire_problem",
        "lead_snapshots",
        "data_revenire_problem IN ('missing', 'name_mismatch', 'unexpected_value')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_lead_snapshots_data_revenire_problem", "lead_snapshots", type_="check")
    op.drop_column("lead_snapshots", "data_revenire_problem")
