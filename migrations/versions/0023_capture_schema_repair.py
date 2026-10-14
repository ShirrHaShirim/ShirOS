"""Repair both earlier variants of the stamped capture migration without changing user rows."""

from alembic import op

revision = "0023_capture_schema_repair"
down_revision = "0022_capture_reauthorization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE capture_entries ADD COLUMN IF NOT EXISTS
          archived boolean NOT NULL DEFAULT false;
        ALTER TABLE capture_entries DROP CONSTRAINT IF EXISTS
          capture_entries_grant_id_message_id_part_key;
        CREATE UNIQUE INDEX IF NOT EXISTS capture_entries_active_message
          ON capture_entries(grant_id,message_id,part) WHERE NOT archived;
    """)


def downgrade() -> None:
    raise RuntimeError("Capture contains user records; use a forward migration.")
