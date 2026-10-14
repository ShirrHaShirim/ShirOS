"""Archive objects while retaining immutable histories and foreign-key targets."""

from alembic import op

revision = "0026_music_archive"
down_revision = "0025_music_profile_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE music_objects ADD COLUMN archived boolean NOT NULL DEFAULT false")
    op.execute("ALTER TABLE music_objects RENAME TO music_object_records")
    # This simple view remains automatically updatable. All library/profile queries
    # see active objects; foreign keys still reference the original physical table.
    op.execute(
        "CREATE VIEW music_objects AS SELECT * FROM music_object_records "
        "WHERE NOT archived WITH LOCAL CHECK OPTION"
    )
    op.execute("""CREATE OR REPLACE VIEW music_work_paths AS
      WITH RECURSIVE edges AS (
        SELECT r.scope_id,r.subject_id AS a,r.object_id AS b FROM music_relations r
        JOIN music_objects a ON a.id=r.subject_id JOIN music_objects b ON b.id=r.object_id
        WHERE r.role IN ('work','recording','parent_work')
        UNION SELECT t.scope_id,t.release_id,t.id FROM music_tracks t
        JOIN music_objects a ON a.id=t.release_id JOIN music_objects b ON b.id=t.id
      ), paths(scope_id,entity_id,node_id) AS (
        SELECT scope_id,id,id FROM music_objects
        UNION SELECT p.scope_id,p.entity_id,e.b FROM paths p JOIN edges e
        ON e.scope_id=p.scope_id AND e.a=p.node_id
      ) SELECT DISTINCT p.scope_id,p.entity_id,p.node_id AS work_id
      FROM paths p JOIN music_objects o ON o.id=p.node_id AND o.scope_id=p.scope_id
      WHERE o.kind='work' """)


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup to preserve archived music history.")
