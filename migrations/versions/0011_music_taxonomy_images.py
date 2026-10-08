"""Separate music taxonomy from memory tags; scoped immutable imported images."""

from alembic import op

revision = "0011_music_taxonomy_images"
down_revision = "0010_music_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TABLE music_taxonomy_bootstrap (scope_id uuid PRIMARY KEY)")
    op.execute("""CREATE TABLE music_taxonomy (
        id uuid PRIMARY KEY, scope_id uuid NOT NULL,
        namespace text NOT NULL CHECK(namespace IN ('tag','genre')),
        name varchar(64) NOT NULL CHECK(length(trim(name))>0), parent_id uuid,
        revision integer NOT NULL DEFAULT 1 CHECK(revision>0),
        created_by uuid NOT NULL REFERENCES local_identities(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(id,scope_id,namespace), CHECK(parent_id IS DISTINCT FROM id),
        FOREIGN KEY(parent_id,scope_id,namespace) REFERENCES music_taxonomy(id,scope_id,namespace)
    )""")
    op.execute("""CREATE UNIQUE INDEX music_taxonomy_sibling ON music_taxonomy
        (scope_id,namespace,coalesce(parent_id,'00000000-0000-0000-0000-000000000000'),lower(name))""")
    op.execute("""CREATE TABLE music_taxonomy_links (
        object_id uuid NOT NULL, scope_id uuid NOT NULL, namespace text NOT NULL,
        node_id uuid NOT NULL, PRIMARY KEY(object_id,node_id),
        FOREIGN KEY(object_id,scope_id) REFERENCES music_objects(id,scope_id),
        FOREIGN KEY(node_id,scope_id,namespace) REFERENCES music_taxonomy(id,scope_id,namespace)
    )""")
    # Preserve only used music tags and ancestors. Do not guess their musical genre.
    op.execute("""WITH RECURSIVE used AS (
        SELECT t.* FROM memory_tags t JOIN music_tag_links l ON l.tag_id=t.id
        UNION SELECT p.* FROM memory_tags p JOIN used child ON child.parent_id=p.id
    ) INSERT INTO music_taxonomy(id,scope_id,namespace,name,parent_id,created_by,created_at)
        SELECT id,scope_id,'tag',name,parent_id,created_by,created_at FROM used""")
    op.execute("""INSERT INTO music_taxonomy_links
        SELECT object_id,scope_id,'tag',tag_id FROM music_tag_links""")
    op.execute("""CREATE TABLE music_taxonomy_history (
        id uuid PRIMARY KEY, node_id uuid NOT NULL REFERENCES music_taxonomy(id),
        scope_id uuid NOT NULL, revision integer NOT NULL, snapshot jsonb NOT NULL,
        created_by uuid NOT NULL REFERENCES local_identities(id),
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(node_id,revision)
    )""")
    op.execute("""INSERT INTO music_taxonomy_history
        (id,node_id,scope_id,revision,snapshot,created_by)
        SELECT id,id,scope_id,revision,to_jsonb(t),created_by FROM music_taxonomy t""")
    op.execute("""CREATE TABLE music_images (
        id uuid PRIMARY KEY, scope_id uuid NOT NULL, source_id uuid NOT NULL,
        created_by uuid NOT NULL REFERENCES local_identities(id),
        filename text NOT NULL, content bytea NOT NULL, sha256 char(64) NOT NULL,
        media_type text NOT NULL CHECK(media_type='image/webp'),
        width integer NOT NULL CHECK(width>0 AND width<=2048),
        height integer NOT NULL CHECK(height>0 AND height<=2048),
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(id,scope_id),
        FOREIGN KEY(source_id,scope_id) REFERENCES sources(id,scope_id)
    )""")
    op.execute("ALTER TABLE music_objects ADD COLUMN image_id uuid")
    op.execute("""ALTER TABLE music_objects ADD CONSTRAINT music_scoped_image
        FOREIGN KEY(image_id,scope_id) REFERENCES music_images(id,scope_id)""")
    for table in ("music_images", "music_taxonomy_history"):
        op.execute(
            f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION shiros_immutable_history()"
        )


def downgrade() -> None:
    raise RuntimeError("Music images and classification history require a forward migration.")
