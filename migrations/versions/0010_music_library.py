"""Native music domain sharing identity, provenance, tags and event ordering."""

from alembic import op

revision = "0010_music_library"
down_revision = "0009_memory_edit_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("events_event_type_check", "events", type_="check")
    op.create_check_constraint(
        "events_event_type_check",
        "events",
        "event_type IN ('memory.created','memory.revised','summary.created',"
        "'snapshot.created','inference.created','music.changed')",
    )
    op.execute("""CREATE TABLE music_objects (
        id uuid PRIMARY KEY, scope_id uuid NOT NULL, kind text NOT NULL,
        title text NOT NULL CHECK(length(trim(title))>0),
        revision integer NOT NULL DEFAULT 1 CHECK(revision>0),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        CHECK(kind IN ('release','work','recording','person','group','organization','track')),
        UNIQUE(id,scope_id), FOREIGN KEY(id,scope_id) REFERENCES entities(id,scope_id)
    )""")
    op.execute("""CREATE TABLE music_works (
        id uuid PRIMARY KEY REFERENCES music_objects(id), catalogue text,
        parent_id uuid REFERENCES music_works(id)
    )""")
    op.execute("""CREATE TABLE music_recordings (
        id uuid PRIMARY KEY REFERENCES music_objects(id), recorded_on date, location text
    )""")
    op.execute("""CREATE TABLE music_releases (
        id uuid PRIMARY KEY REFERENCES music_objects(id), scope_id uuid NOT NULL,
        description text NOT NULL DEFAULT '',
        release_year integer CHECK(release_year BETWEEN 1 AND 9999), catalogue text,
        barcode text, status text CHECK(status IN ('wishlist','listening','listened')),
        douban_id text, douban_status text CHECK(douban_status IN
        ('wishlist','listening','listened')),
        douban_rating integer CHECK(douban_rating BETWEEN 1 AND 5),
        douban_comment text, marked_at text, UNIQUE(scope_id,douban_id),
        FOREIGN KEY(id,scope_id) REFERENCES music_objects(id,scope_id)
    )""")
    op.execute("""CREATE TABLE music_tracks (
        id uuid PRIMARY KEY REFERENCES music_objects(id), release_id uuid NOT NULL,
        scope_id uuid NOT NULL, disc integer NOT NULL DEFAULT 1 CHECK(disc>0),
        position integer NOT NULL CHECK(position>0),
        duration_seconds integer CHECK(duration_seconds>0),
        UNIQUE(release_id,disc,position), FOREIGN KEY(release_id,scope_id)
        REFERENCES music_objects(id,scope_id)
    )""")
    op.execute("""CREATE TABLE music_relations (
        scope_id uuid NOT NULL, subject_id uuid NOT NULL, object_id uuid NOT NULL,
        role text NOT NULL CHECK(role IN ('composer','conductor','performer','singer',
        'ensemble','label','work','recording','reissue_of','parent_work')),
        PRIMARY KEY(subject_id,object_id,role),
        FOREIGN KEY(subject_id,scope_id) REFERENCES music_objects(id,scope_id),
        FOREIGN KEY(object_id,scope_id) REFERENCES music_objects(id,scope_id)
    )""")
    op.execute("""CREATE TABLE music_tag_links (
        scope_id uuid NOT NULL, object_id uuid NOT NULL, tag_id uuid NOT NULL,
        PRIMARY KEY(object_id,tag_id),
        FOREIGN KEY(object_id,scope_id) REFERENCES music_objects(id,scope_id),
        FOREIGN KEY(tag_id,scope_id) REFERENCES memory_tags(id,scope_id)
    )""")
    op.execute("""CREATE TABLE music_reviews (
        id uuid PRIMARY KEY, scope_id uuid NOT NULL, object_id uuid NOT NULL,
        rating integer CHECK(rating BETWEEN 1 AND 5), comment text NOT NULL,
        origin text NOT NULL CHECK(origin IN ('manual','douban')), source_id uuid NOT NULL,
        created_by uuid NOT NULL REFERENCES local_identities(id),
        sequence bigint NOT NULL UNIQUE REFERENCES events(sequence),
        created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY(object_id,scope_id) REFERENCES music_objects(id,scope_id),
        FOREIGN KEY(source_id,scope_id) REFERENCES sources(id,scope_id)
    )""")
    op.execute("""CREATE TABLE music_memberships (
        id uuid PRIMARY KEY, scope_id uuid NOT NULL, object_id uuid NOT NULL,
        library text NOT NULL CHECK(library IN ('featured','frequent')), active boolean NOT NULL,
        reason text NOT NULL, position integer NOT NULL DEFAULT 0,
        sequence bigint NOT NULL UNIQUE REFERENCES events(sequence),
        created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY(object_id,scope_id) REFERENCES music_objects(id,scope_id)
    )""")
    op.execute("""CREATE TABLE music_listening_events (
        id uuid PRIMARY KEY, scope_id uuid NOT NULL, object_id uuid NOT NULL,
        started_at timestamptz NOT NULL, timezone text NOT NULL,
        actual_seconds integer CHECK(actual_seconds>0), platform text NOT NULL,
        source_event_id text, source_id uuid NOT NULL, completed boolean NOT NULL DEFAULT false,
        created_at timestamptz NOT NULL DEFAULT now(),
        FOREIGN KEY(object_id,scope_id) REFERENCES music_objects(id,scope_id),
        FOREIGN KEY(source_id,scope_id) REFERENCES sources(id,scope_id),
        UNIQUE(scope_id,platform,source_event_id)
    )""")
    op.execute("""CREATE TABLE music_playback_sources (
        id uuid PRIMARY KEY, scope_id uuid NOT NULL, object_id uuid NOT NULL,
        url text NOT NULL, platform text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(object_id,url),
        FOREIGN KEY(object_id,scope_id) REFERENCES music_objects(id,scope_id)
    )""")
    op.execute("""CREATE TABLE music_history (
        id uuid PRIMARY KEY, scope_id uuid NOT NULL, object_id uuid NOT NULL,
        revision integer NOT NULL, action text NOT NULL, snapshot jsonb NOT NULL,
        source_id uuid NOT NULL, created_by uuid NOT NULL REFERENCES local_identities(id),
        sequence bigint NOT NULL UNIQUE REFERENCES events(sequence),
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(object_id,revision),
        FOREIGN KEY(object_id,scope_id) REFERENCES music_objects(id,scope_id),
        FOREIGN KEY(source_id,scope_id) REFERENCES sources(id,scope_id)
    )""")
    op.execute("CREATE INDEX music_scope_title ON music_objects(scope_id,lower(title))")
    op.execute("CREATE INDEX music_listening_time ON music_listening_events(scope_id,started_at)")
    for table in ("music_reviews", "music_memberships", "music_listening_events", "music_history"):
        op.execute(
            f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION shiros_immutable_history()"
        )


def downgrade() -> None:
    raise RuntimeError("Music history requires a forward migration; restore backups separately.")
