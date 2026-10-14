"""Additive governed knowledge layers; leave every L2 record untouched."""

from alembic import op

revision = "0014_knowledge_layers"
down_revision = "0013_file_audit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE workspace_preferences (
            scope_id uuid NOT NULL, actor_id uuid NOT NULL REFERENCES local_identities(id),
            theme text NOT NULL CHECK(theme IN ('ivory','forest','lapis','plum','graphite')),
            updated_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(scope_id,actor_id)
        );
        CREATE TABLE knowledge_domains (
            id uuid NOT NULL, scope_id uuid NOT NULL, music boolean NOT NULL DEFAULT false,
            PRIMARY KEY(id,scope_id), FOREIGN KEY(id,scope_id) REFERENCES memory_tags(id,scope_id)
        );
        CREATE UNIQUE INDEX one_music_canonical_domain ON knowledge_domains(scope_id) WHERE music;
        CREATE TABLE knowledge_heads (
            id uuid PRIMARY KEY, scope_id uuid NOT NULL,
            layer text NOT NULL CHECK(layer IN ('L0','L1')),
            domain_key text NOT NULL, revision integer NOT NULL DEFAULT 0,
            checkpoint text, UNIQUE(scope_id,layer,domain_key), UNIQUE(id,scope_id)
        );
        CREATE TABLE knowledge_jobs (
            id uuid PRIMARY KEY, scope_id uuid NOT NULL, head_id uuid NOT NULL,
            request_id uuid NOT NULL, request_hash text NOT NULL,
            created_by uuid NOT NULL REFERENCES local_identities(id),
            reason text NOT NULL CHECK(reason IN ('manual','daily','weekly','milestone','rebuild')),
            state text NOT NULL CHECK(state IN
                ('queued','claimed','awaiting_review','published','cancelled')),
            base_revision integer NOT NULL, fingerprint text NOT NULL, dependencies jsonb NOT NULL,
            claim_actor uuid REFERENCES local_identities(id), lease_until timestamptz,
            progress jsonb NOT NULL DEFAULT '{}', draft jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE(scope_id,created_by,request_id),
            FOREIGN KEY(head_id,scope_id) REFERENCES knowledge_heads(id,scope_id)
        );
        CREATE UNIQUE INDEX one_active_knowledge_job ON knowledge_jobs(head_id)
            WHERE state IN ('queued','claimed','awaiting_review');
        CREATE TABLE knowledge_versions (
            id uuid PRIMARY KEY, scope_id uuid NOT NULL, head_id uuid NOT NULL,
            revision integer NOT NULL, job_id uuid NOT NULL REFERENCES knowledge_jobs(id),
            body jsonb NOT NULL, dependencies jsonb NOT NULL, fingerprint text NOT NULL,
            created_by uuid NOT NULL REFERENCES local_identities(id),
            approved_by uuid NOT NULL REFERENCES local_identities(id),
            evidence_level text NOT NULL DEFAULT 'model_guess' CHECK(evidence_level='model_guess'),
            created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(head_id,revision),
            FOREIGN KEY(head_id,scope_id) REFERENCES knowledge_heads(id,scope_id)
        );
        CREATE TABLE knowledge_audit (
            id uuid PRIMARY KEY, scope_id uuid NOT NULL,
            actor_id uuid NOT NULL REFERENCES local_identities(id),
            subject_id uuid NOT NULL, action text NOT NULL, detail jsonb NOT NULL DEFAULT '{}',
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE TRIGGER immutable_knowledge_versions BEFORE UPDATE OR DELETE ON knowledge_versions
            FOR EACH ROW EXECUTE FUNCTION shiros_immutable_history();
        CREATE TRIGGER immutable_knowledge_audit BEFORE UPDATE OR DELETE ON knowledge_audit
            FOR EACH ROW EXECUTE FUNCTION shiros_immutable_history();
    """)


def downgrade() -> None:
    # Only remove this additive feature on an isolated recovery copy; no legacy table is rewritten.
    op.execute("""
        DROP TABLE knowledge_audit,knowledge_versions,knowledge_jobs,knowledge_heads,
            knowledge_domains,workspace_preferences;
    """)
