from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

# raw_known_keys из config/status-mapping.yaml на момент миграции: до белого списка незнакомый
# ключ лида писался в raw, а в нём мог оказаться новый контакт клиента.
LEAD_KNOWN_KEYS = (
    "assigned_to",
    "business",
    "client_type",
    "company",
    "converted_at",
    "created_at",
    "created_by",
    "custom_fields",
    "description",
    "elimination",
    "email",
    "estimated_value",
    "gclid",
    "groups",
    "id",
    "identity",
    "is_duplicate",
    "is_public",
    "last_contact_at",
    "lifecycle",
    "location",
    "name",
    "phone",
    "priority",
    "source",
    "status",
    "status_changed_at",
    "title",
    "website",
)


def upgrade() -> None:
    known = ", ".join(f"'{key}'" for key in LEAD_KNOWN_KEYS)
    op.execute(
        "UPDATE lead_snapshots SET raw = ("
        "SELECT coalesce(jsonb_object_agg(key, value), '{}'::jsonb) "
        f"FROM jsonb_each(raw) WHERE key IN ({known})"
        ") "
        "WHERE jsonb_typeof(raw) = 'object' AND EXISTS ("
        f"SELECT 1 FROM jsonb_object_keys(raw) AS stored(key) WHERE key NOT IN ({known})"
        ")"
    )
    # detailed_reason это свободный текст продавца о клиенте.
    op.execute(
        "UPDATE lead_snapshots SET raw = raw #- '{elimination,detailed_reason}' "
        "WHERE jsonb_typeof(raw -> 'elimination') = 'object'"
    )


def downgrade() -> None:
    # Вырезанные значения не восстановить, и возвращать их в базу не нужно.
    pass
