from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

# raw_custom_fields.keep из config/status-mapping.yaml на момент миграции: textarea со свободным
# текстом продавца о клиенте (в нём бывают телефоны) из уже записанных снапшотов уходят.
LEAD_KEPT_FIELD_IDS = (5, 14, 20, 38, 39, 40, 41)
CLIENT_KEPT_FIELD_IDS = (12, 15, 33, 43, 47, 48, 49, 50)


def keep_custom_fields(table: str, kept_field_ids: tuple[int, ...]) -> None:
    kept = ", ".join(f"'{field_id}'" for field_id in kept_field_ids)
    op.execute(
        f"UPDATE {table} SET raw = jsonb_set(raw, '{{custom_fields}}', ("
        "SELECT coalesce(jsonb_agg(field ORDER BY position), '[]'::jsonb) "
        "FROM jsonb_array_elements(raw -> 'custom_fields') "
        "WITH ORDINALITY AS kept(field, position) "
        f"WHERE jsonb_typeof(field -> 'field_id') = 'number' AND field ->> 'field_id' IN ({kept})"
        ")) "
        "WHERE jsonb_typeof(raw -> 'custom_fields') = 'array'"
    )
    op.execute(
        f"UPDATE {table} SET raw = jsonb_set(raw, '{{custom_fields}}', 'null'::jsonb) "
        "WHERE jsonb_typeof(raw -> 'custom_fields') NOT IN ('array', 'null')"
    )


def upgrade() -> None:
    keep_custom_fields("lead_snapshots", LEAD_KEPT_FIELD_IDS)
    keep_custom_fields("client_snapshots", CLIENT_KEPT_FIELD_IDS)


def downgrade() -> None:
    # Вырезанный текст не восстановить, и возвращать его в базу не нужно.
    pass
