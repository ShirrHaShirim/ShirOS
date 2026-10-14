"""Preserve music IDs and history; add seven-star provenance and derived genres."""

import sqlalchemy as sa
from alembic import op

revision = "0016_music_v070"
down_revision = "0015_white_theme"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "music_listening_events",
        sa.Column("event_kind", sa.Text(), nullable=False, server_default="unknown"),
    )
    op.create_check_constraint(
        "music_listening_kind_check",
        "music_listening_events",
        "event_kind IN ('unknown','manual','playback','external')",
    )
    # Existing reviews remain in their original five-star system.
    op.add_column(
        "music_reviews", sa.Column("rating_max", sa.Integer(), nullable=False, server_default="5")
    )
    op.drop_constraint("music_reviews_rating_check", "music_reviews", type_="check")
    op.create_check_constraint(
        "music_reviews_rating_check",
        "music_reviews",
        "rating_max IN (5,7) AND (rating IS NULL OR rating BETWEEN 1 AND rating_max)",
    )
    op.execute("""CREATE TABLE music_profiles (
        id uuid PRIMARY KEY, scope_id uuid NOT NULL, data jsonb NOT NULL DEFAULT '{}',
        FOREIGN KEY(id,scope_id) REFERENCES music_objects(id,scope_id)
    )""")
    op.execute("""ALTER TABLE music_relations DROP CONSTRAINT music_relations_role_check""")
    op.execute("""ALTER TABLE music_relations ADD CONSTRAINT music_relations_role_check
        CHECK(role IN ('composer','conductor','performer','singer','ensemble','label',
        'work','recording','reissue_of','parent_work','publisher','member','former_member',
        'chief_conductor','suborganization','collaborator'))""")
    op.execute("""CREATE TABLE music_relation_details (
        scope_id uuid NOT NULL, subject_id uuid NOT NULL, object_id uuid NOT NULL,
        role text NOT NULL, started_on date, ended_on date, source_text text NOT NULL DEFAULT '',
        PRIMARY KEY(subject_id,object_id,role),
        FOREIGN KEY(subject_id,object_id,role) REFERENCES
        music_relations(subject_id,object_id,role) ON DELETE CASCADE,
        FOREIGN KEY(subject_id,scope_id) REFERENCES music_objects(id,scope_id),
        CHECK(ended_on IS NULL OR started_on IS NULL OR ended_on>=started_on)
    )""")
    # Traverse actual relations and track containment. Album identity is never reclassified.
    op.execute("""CREATE VIEW music_work_paths AS
        WITH RECURSIVE edges AS (
            SELECT scope_id,subject_id AS a,object_id AS b FROM music_relations
            WHERE role IN ('work','recording')
            UNION SELECT scope_id,release_id,id FROM music_tracks
        ), paths(scope_id,entity_id,node_id) AS (
            SELECT scope_id,id,id FROM music_objects
            UNION SELECT p.scope_id,p.entity_id,e.b FROM paths p JOIN edges e
            ON e.scope_id=p.scope_id AND e.a=p.node_id
        ) SELECT DISTINCT p.scope_id,p.entity_id,p.node_id AS work_id
        FROM paths p JOIN music_objects o ON o.id=p.node_id AND o.scope_id=p.scope_id
        WHERE o.kind='work'""")
    op.execute("""CREATE VIEW music_effective_genres AS
        SELECT DISTINCT p.scope_id,p.entity_id AS object_id,l.node_id
        FROM music_work_paths p JOIN music_taxonomy_links l
        ON l.object_id=p.work_id AND l.scope_id=p.scope_id AND l.namespace='genre'""")


def downgrade() -> None:
    raise RuntimeError("Restore the verified pre-v0.7.0 backup; never discard music history.")
