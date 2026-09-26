import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "schedules",
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column("schedules", sa.Column("updated_by", sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column("schedules", "updated_by")
    op.drop_column("schedules", "updated_at")
