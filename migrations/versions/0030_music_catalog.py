"""Shared virtual work catalogue; assignments never create musical Works."""

from alembic import op

revision = "0030_music_catalog"
down_revision = "0029_music_arranger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE FUNCTION music_catalog_sort_key(value text) RETURNS text[]
      LANGUAGE sql IMMUTABLE STRICT AS $$
      SELECT array_agg(CASE WHEN part[1] ~ '^[0-9]+$'
        THEN '1' || lpad(length(coalesce(nullif(ltrim(part[1],'0'),''),'0'))::text,6,'0')
          || coalesce(nullif(ltrim(part[1],'0'),''),'0')
        ELSE '0' || part[1] END ORDER BY ord)
      FROM regexp_matches(lower(value),'[0-9]+|[^0-9]+','g') WITH ORDINALITY AS chunks(part,ord)
      $$""")
    op.execute("""CREATE TABLE music_catalog_categories (
      code text PRIMARY KEY, name_zh text NOT NULL, name_en text NOT NULL,
      parent_code text REFERENCES music_catalog_categories(code), position integer NOT NULL,
      CHECK(code<>coalesce(parent_code,'')), UNIQUE(parent_code,position)
    )""")
    op.execute("""CREATE TABLE music_catalog_assignments (
      scope_id uuid NOT NULL, work_id uuid NOT NULL, category_code text NOT NULL,
      is_primary boolean NOT NULL, origin text NOT NULL CHECK(origin IN
      ('manual','genre','migration')),
      source_text text NOT NULL, revision integer NOT NULL CHECK(revision>0),
      updated_by uuid NOT NULL REFERENCES local_identities(id), updated_at timestamptz NOT NULL,
      PRIMARY KEY(work_id,category_code),
      FOREIGN KEY(work_id,scope_id) REFERENCES music_object_records(id,scope_id),
      FOREIGN KEY(category_code) REFERENCES music_catalog_categories(code)
    )""")
    op.execute(
        "CREATE UNIQUE INDEX music_catalog_primary ON music_catalog_assignments(work_id) "
        "WHERE is_primary"
    )
    op.execute(
        "CREATE INDEX music_catalog_scope_category ON "
        "music_catalog_assignments(scope_id,category_code,work_id)"
    )
    op.execute("""CREATE TABLE music_catalog_history (
      id uuid PRIMARY KEY, scope_id uuid NOT NULL, work_id uuid NOT NULL,
      revision integer NOT NULL, snapshot jsonb NOT NULL, source_id uuid NOT NULL,
      created_by uuid NOT NULL REFERENCES local_identities(id), created_at timestamptz NOT
      NULL DEFAULT now(),
      UNIQUE(work_id,revision),
      FOREIGN KEY(work_id,scope_id) REFERENCES music_object_records(id,scope_id),
      FOREIGN KEY(source_id,scope_id) REFERENCES sources(id,scope_id)
    )""")
    op.execute("""CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON music_catalog_history
      FOR EACH ROW EXECUTE FUNCTION shiros_immutable_history()""")
    op.execute("""CREATE TABLE music_catalog_genre_map (
      scope_id uuid NOT NULL, node_id uuid NOT NULL, namespace text NOT NULL DEFAULT 'genre'
      CHECK(namespace='genre'),
      category_code text NOT NULL REFERENCES music_catalog_categories(code),
      source_text text NOT NULL, PRIMARY KEY(node_id),
      FOREIGN KEY(node_id,scope_id,namespace) REFERENCES music_taxonomy(id,scope_id,namespace)
    )""")
    op.execute("""CREATE TABLE music_catalog_redirects (
      work_id uuid PRIMARY KEY, scope_id uuid NOT NULL, composer_id uuid NOT NULL,
      category_code text NOT NULL REFERENCES music_catalog_categories(code),
      source_id uuid NOT NULL, snapshot jsonb NOT NULL, created_at timestamptz NOT NULL
      DEFAULT now(),
      FOREIGN KEY(work_id,scope_id) REFERENCES music_object_records(id,scope_id),
      FOREIGN KEY(composer_id,scope_id) REFERENCES music_object_records(id,scope_id),
      FOREIGN KEY(source_id,scope_id) REFERENCES sources(id,scope_id)
    )""")
    op.execute("""CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON
    music_catalog_redirects
      FOR EACH ROW EXECUTE FUNCTION shiros_immutable_history()""")
    from shiros.domains.music_catalog import seed_categories

    seed_categories(op.get_bind())


def downgrade() -> None:
    raise RuntimeError(
        "Restore a verified backup to retain catalogue and archived reference history."
    )
