"""File operations have an append-only audit separate from memory review."""

from alembic import op

revision = "0013_file_audit"
down_revision = "0012_file_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE file_audit (
            id uuid PRIMARY KEY, scope_id uuid NOT NULL,
            actor_id uuid NOT NULL REFERENCES local_identities(id),
            subject_id uuid NOT NULL, action text NOT NULL
                CHECK(action IN ('file.upload','file.conversation-import','file.delete')),
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TRIGGER immutable_file_audit BEFORE UPDATE OR DELETE ON file_audit
            FOR EACH ROW EXECUTE FUNCTION shiros_immutable_history();
    """)


def downgrade() -> None:
    raise RuntimeError("File audit requires a forward migration")
