"""Scoped hierarchical tags without changing immutable memory revisions."""

from alembic import op

revision = "0006_memory_tags"
down_revision = "0005_agent_candidates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE memory_tags (
        id uuid PRIMARY KEY, scope_id uuid NOT NULL, name varchar(64) NOT NULL,
        parent_id uuid, created_by uuid NOT NULL REFERENCES local_identities(id),
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id,scope_id),
        CHECK(length(trim(name)) > 0), CHECK(parent_id IS DISTINCT FROM id),
        FOREIGN KEY(parent_id,scope_id) REFERENCES memory_tags(id,scope_id)
    )""")
    op.execute(
        "CREATE UNIQUE INDEX tag_sibling_name ON memory_tags "
        "(scope_id,coalesce(parent_id,'00000000-0000-0000-0000-000000000000'::uuid),lower(name))"
    )
    op.execute("""CREATE TABLE memory_tag_links (
        scope_id uuid NOT NULL, memory_id uuid NOT NULL, tag_id uuid NOT NULL,
        created_by uuid NOT NULL REFERENCES local_identities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(scope_id,memory_id,tag_id),
        FOREIGN KEY(tag_id,scope_id) REFERENCES memory_tags(id,scope_id)
    )""")
    op.execute("""CREATE TABLE tag_audit (
        id bigserial PRIMARY KEY, scope_id uuid NOT NULL, actor_id uuid NOT NULL,
        subject_id uuid NOT NULL,
        action text NOT NULL CHECK(action IN ('create','update','add','replace')),
        created_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute(
        "CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON tag_audit "
        "FOR EACH ROW EXECUTE FUNCTION shiros_immutable_history()"
    )


def downgrade() -> None:
    op.drop_table("tag_audit")
    op.drop_table("memory_tag_links")
    op.drop_table("memory_tags")
