import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Старые строки NULL: замера кэша не было, а 0 значил бы «кэш не читался».
    op.add_column("chat_questions", sa.Column("cache_creation_input_tokens", sa.Integer()))
    op.add_column("chat_questions", sa.Column("cache_read_input_tokens", sa.Integer()))


def downgrade() -> None:
    op.drop_column("chat_questions", "cache_read_input_tokens")
    op.drop_column("chat_questions", "cache_creation_input_tokens")
