"""Local identity, staged sources/candidates, audit and reversible visibility."""

from alembic import op

revision = "0003_trusted_review"
down_revision = "0002_shared_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE local_identities (
        id uuid PRIMARY KEY, principal text NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE TABLE local_grants (
        actor_id uuid REFERENCES local_identities(id), scope_id uuid NOT NULL,
        role text NOT NULL CHECK(role IN ('owner','reviewer','reader','worker')),
        PRIMARY KEY(actor_id,scope_id)
    )""")
    op.execute("""CREATE TABLE review_sources (
        id uuid PRIMARY KEY, scope_id uuid NOT NULL,
        schema_version integer NOT NULL DEFAULT 1 CHECK(schema_version=1),
        kind text NOT NULL CHECK(kind IN ('text','markdown','json','csv')),
        origin text NOT NULL CHECK(origin='manual-local-file'), title text NOT NULL,
        text text NOT NULL, content_hash char(64) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(), observed_at timestamptz NOT NULL,
        created_by uuid NOT NULL REFERENCES local_identities(id),
        privacy_state text NOT NULL CHECK(privacy_state='staging_allowed'),
        sensitivity text NOT NULL CHECK(sensitivity IN ('ordinary','personal')),
        UNIQUE(scope_id,content_hash), UNIQUE(id,scope_id)
    )""")
    op.execute("""CREATE TABLE memory_candidates (
        id uuid PRIMARY KEY, scope_id uuid NOT NULL, source_id uuid NOT NULL,
        schema_version integer NOT NULL DEFAULT 1 CHECK(schema_version=1),
        original_text text NOT NULL,
        fact_level text NOT NULL CHECK(fact_level='explicit_statement'),
        confidence double precision NOT NULL CHECK(confidence BETWEEN 0 AND 1),
        created_at timestamptz NOT NULL DEFAULT now(),
        created_by uuid NOT NULL REFERENCES local_identities(id),
        UNIQUE(source_id), UNIQUE(id,scope_id),
        FOREIGN KEY(source_id,scope_id) REFERENCES review_sources(id,scope_id)
    )""")
    op.execute("""CREATE TABLE candidate_states (
        id uuid PRIMARY KEY, candidate_id uuid NOT NULL REFERENCES memory_candidates(id),
        revision integer NOT NULL CHECK(revision>0),
        status text NOT NULL
          CHECK(status IN ('pending','edited','approved','rejected','invalidated')),
        text text NOT NULL, actor_id uuid NOT NULL REFERENCES local_identities(id),
        created_at timestamptz NOT NULL DEFAULT now(), reason_code text NOT NULL
          CHECK(reason_code IN
            ('manual_stage','manual_edit','manual_approve','manual_reject','source_revoked')),
        memory_revision_id uuid REFERENCES memories(id),
        CHECK ((status='approved') = (memory_revision_id IS NOT NULL)),
        UNIQUE(candidate_id,revision)
    )""")
    op.execute("""CREATE TABLE review_source_links (
        intake_source_id uuid NOT NULL REFERENCES review_sources(id),
        source_id uuid NOT NULL REFERENCES sources(id), PRIMARY KEY(intake_source_id,source_id)
    )""")
    op.execute("""CREATE TABLE review_audit (
        id uuid PRIMARY KEY, scope_id uuid NOT NULL,
        actor_id uuid NOT NULL REFERENCES local_identities(id),
        subject_id uuid NOT NULL, action text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK(action IN
          ('stage','edit','approve','reject','revoke','hide','show','grant','bootstrap'))
    )""")
    op.execute("""CREATE TABLE revocations (
        id uuid PRIMARY KEY, scope_id uuid NOT NULL, target_id uuid NOT NULL,
        kind text NOT NULL CHECK(kind IN ('source','memory','intake_source')),
        actor_id uuid NOT NULL REFERENCES local_identities(id),
        created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(kind,target_id)
    )""")
    op.execute("""CREATE TABLE memory_visibility (
        id bigserial PRIMARY KEY, scope_id uuid NOT NULL, memory_id uuid NOT NULL,
        hidden boolean NOT NULL, actor_id uuid NOT NULL REFERENCES local_identities(id),
        created_at timestamptz NOT NULL DEFAULT now()
    )""")
    for table in (
        "review_sources",
        "memory_candidates",
        "candidate_states",
        "review_source_links",
        "review_audit",
        "revocations",
        "memory_visibility",
    ):
        op.execute(
            f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION shiros_immutable_history()"
        )


def downgrade() -> None:
    for table in (
        "memory_visibility",
        "revocations",
        "review_audit",
        "review_source_links",
        "candidate_states",
        "memory_candidates",
        "review_sources",
        "local_grants",
        "local_identities",
    ):
        op.drop_table(table)
