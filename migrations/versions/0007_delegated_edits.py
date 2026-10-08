"""Explicit delegated mutation role and reversible tag deletion."""

from alembic import op

revision = "0007_delegated_edits"
down_revision = "0006_memory_tags"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("local_grants_role_check", "local_grants", type_="check")
    op.create_check_constraint(
        "local_grants_role_check",
        "local_grants",
        "role IN ('owner','reviewer','reader','worker','proposer','editor')",
    )
    op.execute("ALTER TABLE memory_tags ADD COLUMN deleted boolean NOT NULL DEFAULT false")
    op.execute("DROP INDEX tag_sibling_name")
    op.execute(
        "CREATE UNIQUE INDEX tag_sibling_name ON memory_tags "
        "(scope_id,coalesce(parent_id,'00000000-0000-0000-0000-000000000000'::uuid),"
        "lower(name)) WHERE NOT deleted"
    )
    op.drop_constraint("tag_audit_action_check", "tag_audit", type_="check")
    op.create_check_constraint(
        "tag_audit_action_check",
        "tag_audit",
        "action IN ('create','update','add','replace','remove','delete')",
    )


def downgrade() -> None:
    raise RuntimeError(
        "Delegated edit history requires a forward migration; "
        "restore a verified backup instead."
    )
