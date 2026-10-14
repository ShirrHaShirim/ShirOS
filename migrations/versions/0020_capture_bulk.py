"""Track explicit bulk grants and their conversation-scoped children."""

from alembic import op

revision = "0020_capture_bulk"
down_revision = "0019_conversation_capture"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE capture_grants ADD COLUMN is_bulk boolean NOT NULL DEFAULT false;
        ALTER TABLE capture_grants ADD COLUMN parent_grant_id uuid REFERENCES capture_grants(id);
        ALTER TABLE capture_grants ADD CONSTRAINT capture_parent_not_self
            CHECK(parent_grant_id IS NULL OR parent_grant_id<>id);
        CREATE INDEX capture_children_parent ON capture_grants(parent_grant_id);
    """)


def downgrade() -> None:
    raise RuntimeError("Bulk capture contains user records; use a forward migration.")
