import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_questions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column(
            "tool_calls",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("snapshot_dates", postgresql.ARRAY(sa.Date()), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_chat_questions"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_chat_questions_tenant_id_tenants"
        ),
        sa.CheckConstraint(
            "status IN ('answered', 'no_tool', 'blocked_numbers', 'unverified_numbers', "
            "'rate_limited', 'api_error', 'max_tokens', 'refusal')",
            name="ck_chat_questions_status",
        ),
    )
    op.create_index(
        "ix_chat_questions_tenant_id_chat_id_created_at",
        "chat_questions",
        ["tenant_id", "chat_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_questions_tenant_id_chat_id_created_at", table_name="chat_questions")
    op.drop_table("chat_questions")
