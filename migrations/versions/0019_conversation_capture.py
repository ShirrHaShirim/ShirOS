"""Explicit conversation grants and links to memory and archived attachments."""

from alembic import op

revision = "0019_conversation_capture"
down_revision = "0018_music_review_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE capture_grants (
      id uuid PRIMARY KEY, scope_id uuid NOT NULL,
      actor_id uuid NOT NULL REFERENCES local_identities(id),
      provider text NOT NULL
        CHECK(provider IN ('gpt','codex','opencode','deepseek','deepseek-harness')),
      conversation_id text NOT NULL, title text NOT NULL, token_hash text NOT NULL,
      enabled boolean NOT NULL DEFAULT true, include_files boolean NOT NULL DEFAULT false,
      source_path text, attachment_root text, native_endpoint text,
      created_at timestamptz NOT NULL DEFAULT now(),
      revoked_at timestamptz, last_seen_at timestamptz, last_error text
    );
    CREATE UNIQUE INDEX capture_one_active_conversation
      ON capture_grants(scope_id,provider,conversation_id) WHERE enabled;
    CREATE TABLE capture_entries (
      id uuid PRIMARY KEY, grant_id uuid NOT NULL REFERENCES capture_grants(id),
      scope_id uuid NOT NULL,
      message_id text NOT NULL, part integer NOT NULL, content_hash text NOT NULL,
      candidate_id uuid REFERENCES memory_candidates(id), memory_id uuid,
      created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(grant_id,message_id,part)
    );
    CREATE TABLE capture_file_links (
      id uuid PRIMARY KEY, entry_id uuid NOT NULL REFERENCES capture_entries(id),
      file_id uuid REFERENCES library_files(id) ON DELETE SET NULL,
      attachment_id text NOT NULL, filename text NOT NULL, reference text,
      state text NOT NULL CHECK(state IN ('saved','reference','deleted')),
      UNIQUE(entry_id,attachment_id)
    );
    CREATE TABLE capture_audit (
      id uuid PRIMARY KEY, scope_id uuid NOT NULL,
      actor_id uuid NOT NULL REFERENCES local_identities(id),
      grant_id uuid NOT NULL REFERENCES capture_grants(id), action text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    );
    CREATE INDEX capture_entries_scope ON capture_entries(scope_id,memory_id);
    CREATE INDEX capture_links_file ON capture_file_links(file_id);
    """)


def downgrade() -> None:
    raise RuntimeError("Capture contains user records; use a forward migration.")
