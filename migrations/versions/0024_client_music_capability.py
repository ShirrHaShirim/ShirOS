"""Separate owner-authorized music editing from memory client roles."""

from alembic import op

revision = "0024_client_music_capability"
down_revision = "0023_capture_schema_repair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE client_capability_grants (
            actor_id uuid NOT NULL,
            scope_id uuid NOT NULL,
            capability text NOT NULL CHECK (capability IN ('music_write')),
            granted_by uuid NOT NULL REFERENCES local_identities(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY(actor_id,scope_id,capability),
            FOREIGN KEY(actor_id,scope_id) REFERENCES local_grants(actor_id,scope_id)
                ON DELETE CASCADE
        );
    """)


def downgrade() -> None:
    raise RuntimeError("Client grants are owner-managed; use a forward migration.")
