"""Keep old capture generations archived when the owner explicitly authorizes a new import."""

from alembic import op

revision = "0022_capture_reauthorization"
down_revision = "0021_capture_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE capture_entries ADD COLUMN archived boolean NOT NULL DEFAULT false;
        ALTER TABLE capture_entries DROP CONSTRAINT capture_entries_grant_id_message_id_part_key;
        CREATE UNIQUE INDEX capture_entries_active_message
          ON capture_entries(grant_id,message_id,part) WHERE NOT archived;
        ALTER TABLE capture_conversations ADD COLUMN consent_grant_id uuid
          REFERENCES capture_grants(id);
        UPDATE capture_conversations cc SET consent_grant_id=coalesce(g.parent_grant_id,g.id)
          FROM capture_entries e JOIN capture_grants g ON g.id=e.grant_id WHERE e.id=cc.entry_id;
        CREATE TABLE capture_message_archives (
          id uuid PRIMARY KEY, conversation_id uuid NOT NULL REFERENCES capture_conversations(id),
          entry_id uuid REFERENCES capture_entries(id),
          consent_grant_id uuid REFERENCES capture_grants(id),
          messages jsonb NOT NULL, archived_at timestamptz NOT NULL DEFAULT now()
        );
    """)


def downgrade() -> None:
    raise RuntimeError("Capture archives contain user records; use a forward migration.")
