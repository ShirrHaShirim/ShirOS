"""Reversible classification deletion, preserving links and history."""

from alembic import op

revision = "0017_music_tag_deletion"
down_revision = "0016_music_v070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE music_taxonomy ADD COLUMN deleted boolean NOT NULL DEFAULT false")
    op.execute("DROP INDEX music_taxonomy_sibling")
    op.execute(
        "CREATE UNIQUE INDEX music_taxonomy_sibling ON music_taxonomy "
        "(scope_id,namespace,coalesce(parent_id,'00000000-0000-0000-0000-000000000000'),"
        "lower(name)) WHERE NOT deleted"
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup to preserve classification history.")
