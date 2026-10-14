"""Install natural catalogue ordering on services that applied the initial schema early."""

from alembic import op

revision = "0031_music_catalog_sort"
down_revision = "0030_music_catalog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE OR REPLACE FUNCTION music_catalog_sort_key(value text) RETURNS text[]
      LANGUAGE sql IMMUTABLE STRICT AS $$
      SELECT array_agg(CASE WHEN part[1] ~ '^[0-9]+$'
        THEN '1' || lpad(length(coalesce(nullif(ltrim(part[1],'0'),''),'0'))::text,6,'0')
          || coalesce(nullif(ltrim(part[1],'0'),''),'0')
        ELSE '0' || part[1] END ORDER BY ord)
      FROM regexp_matches(lower(value),'[0-9]+|[^0-9]+','g') WITH ORDINALITY AS chunks(part,ord)
      $$""")


def downgrade() -> None:
    raise RuntimeError("Natural catalogue ordering is required by the current application.")
