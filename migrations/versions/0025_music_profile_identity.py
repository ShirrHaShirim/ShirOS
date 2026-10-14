"""Independent identity taxonomy and explicit child-work genre inheritance."""

from alembic import op

revision = "0025_music_profile_identity"
down_revision = "0024_client_music_capability"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE music_taxonomy DROP CONSTRAINT music_taxonomy_namespace_check")
    op.execute(
        "ALTER TABLE music_taxonomy ADD CONSTRAINT music_taxonomy_namespace_check "
        "CHECK(namespace IN ('tag','genre','identity'))"
    )
    op.execute("ALTER TABLE music_taxonomy ADD COLUMN derived_only boolean NOT NULL DEFAULT false")
    op.execute("ALTER TABLE music_taxonomy ADD COLUMN music_class text NOT NULL DEFAULT ''")
    op.execute(
        "ALTER TABLE music_relation_details ADD COLUMN performance_role text NOT NULL DEFAULT ''"
    )
    op.execute("""CREATE OR REPLACE VIEW music_work_paths AS
        WITH RECURSIVE edges AS (
            SELECT scope_id,subject_id AS a,object_id AS b FROM music_relations
            WHERE role IN ('work','recording','parent_work')
            UNION SELECT scope_id,release_id,id FROM music_tracks
        ), paths(scope_id,entity_id,node_id) AS (
            SELECT scope_id,id,id FROM music_objects
            UNION SELECT p.scope_id,p.entity_id,e.b FROM paths p JOIN edges e
            ON e.scope_id=p.scope_id AND e.a=p.node_id
        ) SELECT DISTINCT p.scope_id,p.entity_id,p.node_id AS work_id
        FROM paths p JOIN music_objects o ON o.id=p.node_id AND o.scope_id=p.scope_id
        WHERE o.kind='work'""")


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup to preserve identity and relation history.")
