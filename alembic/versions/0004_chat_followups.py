import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("chat_questions", sa.Column("reply_message_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "chat_questions", sa.Column("context_question_id", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        "fk_chat_questions_context_question_id_chat_questions",
        "chat_questions",
        "chat_questions",
        ["context_question_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_chat_questions_context_question_id_chat_questions",
        "chat_questions",
        type_="foreignkey",
    )
    op.drop_column("chat_questions", "context_question_id")
    op.drop_column("chat_questions", "reply_message_id")
