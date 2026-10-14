"""Imported review dates and append-only review deletion records."""

from alembic import op

revision = "0018_music_review_history"
down_revision = "0017_music_tag_deletion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE music_reviews ADD COLUMN original_at timestamptz")
    op.execute("ALTER TABLE music_reviews ADD COLUMN import_key varchar(64)")
    op.execute("ALTER TABLE music_reviews DROP CONSTRAINT music_reviews_origin_check")
    op.execute(
        "ALTER TABLE music_reviews ADD CONSTRAINT music_reviews_origin_check "
        "CHECK(origin IN ('manual','douban','import','history_manual'))"
    )
    op.execute(
        "CREATE UNIQUE INDEX music_review_import_key ON music_reviews "
        "(scope_id,object_id,import_key) WHERE import_key IS NOT NULL"
    )
    op.execute("""CREATE TABLE music_review_deletions (
        id uuid PRIMARY KEY, scope_id uuid NOT NULL, object_id uuid NOT NULL,
        review_id uuid NOT NULL UNIQUE REFERENCES music_reviews(id),
        source_id uuid NOT NULL, created_by uuid NOT NULL REFERENCES local_identities(id),
        sequence bigint NOT NULL UNIQUE REFERENCES events(sequence),
        deleted_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY(object_id,scope_id) REFERENCES music_objects(id,scope_id),
        FOREIGN KEY(source_id,scope_id) REFERENCES sources(id,scope_id)
    )""")
    op.execute(
        "CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON music_review_deletions "
        "FOR EACH ROW EXECUTE FUNCTION shiros_immutable_history()"
    )


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup to preserve review history.")
