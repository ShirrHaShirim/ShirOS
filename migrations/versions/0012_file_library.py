"""Owner-managed binary file archive, separate from approved memories."""

from alembic import op

revision = "0012_file_library"
down_revision = "0011_music_taxonomy_images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE library_files (
            id uuid PRIMARY KEY,
            scope_id uuid NOT NULL,
            filename text NOT NULL,
            media_type text NOT NULL,
            size bigint NOT NULL CHECK (size BETWEEN 0 AND 52428800),
            sha256 text NOT NULL,
            content bytea NOT NULL,
            created_by uuid NOT NULL REFERENCES local_identities(id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            external_key text,
            UNIQUE(scope_id, external_key)
        );
        CREATE INDEX library_files_scope ON library_files(scope_id, created_at DESC);
    """)


def downgrade() -> None:
    raise RuntimeError("Archive contains user files; use a forward migration")
