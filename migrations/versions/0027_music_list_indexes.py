"""Indexes for latest-review lookup and paged/continuous catalogue browsing."""

from alembic import op

revision = "0027_music_list_indexes"
down_revision = "0026_music_archive"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX music_review_latest ON music_reviews "
        "(object_id,(coalesce(original_at,created_at)) DESC,sequence DESC) "
        "INCLUDE(rating,rating_max)"
    )
    op.execute(
        "CREATE INDEX music_relation_incoming ON music_relations "
        "(scope_id,object_id,role,subject_id)"
    )
    op.execute(
        "CREATE INDEX music_object_catalogue_page ON music_object_records "
        "(scope_id,kind,created_at DESC,id) WHERE NOT archived"
    )


def downgrade() -> None:
    op.execute("DROP INDEX music_object_catalogue_page")
    op.execute("DROP INDEX music_relation_incoming")
    op.execute("DROP INDEX music_review_latest")
