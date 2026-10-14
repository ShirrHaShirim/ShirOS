"""One durable conversation with ordered messages and one public memory."""

from alembic import op

revision = "0021_capture_conversations"
down_revision = "0020_capture_bulk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE capture_conversations (
          id uuid PRIMARY KEY, scope_id uuid NOT NULL, provider text NOT NULL,
          conversation_id text NOT NULL, actor_id uuid NOT NULL REFERENCES local_identities(id),
          entry_id uuid REFERENCES capture_entries(id),
          UNIQUE(scope_id,provider,conversation_id)
        );
        CREATE TABLE capture_messages (
          conversation_id uuid NOT NULL REFERENCES capture_conversations(id),
          message_id text NOT NULL, role text NOT NULL, body text NOT NULL,
          position bigint NOT NULL, observed_at timestamptz,
          attachments jsonb NOT NULL DEFAULT '[]', needs_review boolean NOT NULL DEFAULT false,
          PRIMARY KEY(conversation_id,message_id)
        );
        CREATE TABLE capture_merged_memories (
          memory_id uuid PRIMARY KEY, entry_id uuid NOT NULL REFERENCES capture_entries(id)
        );
        -- A PostgreSQL tsvector has a 1 MB ceiling. Long bodies remain intact;
        -- exact substring search still searches the complete body.
        DROP INDEX IF EXISTS memories_text_idx;
    """)
    # Replace the existing expression index by name, without depending on its historical name.
    op.execute("""
        DO $$ DECLARE idx record; BEGIN
          FOR idx IN SELECT indexname FROM pg_indexes
            WHERE schemaname=current_schema() AND tablename='memories'
              AND indexdef LIKE '%to_tsvector%'
          LOOP EXECUTE format('DROP INDEX %I',idx.indexname); END LOOP;
        END $$;
        CREATE INDEX memories_text_bounded_idx ON memories
          USING gin (to_tsvector('simple',left(text,100000)));
    """)


def downgrade() -> None:
    raise RuntimeError("Conversation archives contain user records; use a forward migration.")
