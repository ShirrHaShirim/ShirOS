"""Shared memory records, immutable history and transaction-ordered events."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql.schema import SchemaItem

revision = "0002_shared_memory"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None

LEVELS = (
    "'fact','explicit_statement','derived_fact','pattern','inference','hypothesis','model_guess'"
)


def common() -> list[SchemaItem]:
    return [
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("scope_id", sa.UUID(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_id", sa.UUID(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("fact_level", sa.Text(), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("sensitivity", sa.Text(), nullable=False),
        sa.Column("confidentiality", sa.Text(), nullable=False),
        sa.Column("persistence_allowed", sa.Boolean(), nullable=False),
        sa.Column("reviewed_by", sa.UUID(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.UniqueConstraint("id", "scope_id"),
        sa.CheckConstraint("schema_version = 1"),
        sa.CheckConstraint(f"fact_level IN ({LEVELS})"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1"),
        sa.CheckConstraint("persistence_allowed AND confidentiality = 'standard'"),
        sa.CheckConstraint("sensitivity IN ('ordinary','personal')"),
        sa.CheckConstraint("policy_version = 'rules-v1'"),
        sa.ForeignKeyConstraint(["source_id", "scope_id"], ["sources.id", "sources.scope_id"]),
    ]


def upgrade() -> None:
    op.create_table(
        "sources",
        *common(),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.CheckConstraint("source_id = id"),
        sa.CheckConstraint("kind IN ('note','synthetic')"),
    )
    op.create_table(
        "entities",
        *common(),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
    )
    op.create_table(
        "artifacts",
        *common(),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.CheckConstraint("media_type = 'text/plain'"),
        sa.UniqueConstraint("source_id"),
    )
    op.create_table(
        "relations",
        *common(),
        sa.Column("from_entity_id", sa.UUID(), nullable=False),
        sa.Column("to_entity_id", sa.UUID(), nullable=False),
        sa.Column("predicate", sa.Text(), nullable=False),
        sa.CheckConstraint("predicate IN ('related_to','supports')"),
        sa.ForeignKeyConstraint(
            ["from_entity_id", "scope_id"], ["entities.id", "entities.scope_id"]
        ),
        sa.ForeignKeyConstraint(["to_entity_id", "scope_id"], ["entities.id", "entities.scope_id"]),
        sa.UniqueConstraint("source_id", "from_entity_id", "to_entity_id", "predicate"),
    )
    op.create_table(
        "memories",
        *common(),
        sa.Column("memory_id", sa.UUID(), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.UUID(), nullable=True, unique=True),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.UniqueConstraint("memory_id", "revision"),
        sa.UniqueConstraint("id", "scope_id", "memory_id"),
        sa.CheckConstraint("revision >= 1"),
        sa.CheckConstraint(
            "(revision = 1 AND supersedes_id IS NULL) OR "
            "(revision > 1 AND supersedes_id IS NOT NULL)"
        ),
        sa.ForeignKeyConstraint(["entity_id", "scope_id"], ["entities.id", "entities.scope_id"]),
        sa.ForeignKeyConstraint(
            ["supersedes_id", "scope_id", "memory_id"],
            ["memories.id", "memories.scope_id", "memories.memory_id"],
        ),
    )
    op.execute("""CREATE TABLE memory_embeddings (
        memory_revision_id uuid PRIMARY KEY REFERENCES memories(id),
        model text NOT NULL CHECK (model = 'hash-bow-32-v1'),
        dimensions integer NOT NULL CHECK (dimensions = 32),
        embedding vector(32) NOT NULL
    )""")
    op.execute("""CREATE INDEX ix_memory_vector ON memory_embeddings
        USING hnsw (embedding vector_cosine_ops)""")
    op.execute("""CREATE INDEX ix_memory_fts ON memories
        USING gin (to_tsvector('simple', text))""")
    op.create_index("ix_memory_scope_time", "memories", ["scope_id", "created_at"])
    op.create_table(
        "events",
        *common(),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("record_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False, unique=True),
        sa.CheckConstraint("sequence >= 1"),
        sa.CheckConstraint(
            "event_type IN ('memory.created','memory.revised',"
            "'summary.created','snapshot.created','inference.created')"
        ),
        sa.ForeignKeyConstraint(["entity_id", "scope_id"], ["entities.id", "entities.scope_id"]),
    )
    op.create_index("ix_events_scope_sequence", "events", ["scope_id", "sequence"])
    op.execute(
        "CREATE TABLE event_clock (id integer PRIMARY KEY CHECK (id = 1), value bigint NOT NULL)"
    )
    op.execute("INSERT INTO event_clock VALUES (1, 0)")
    for name in ("summaries", "snapshots"):
        columns = common() + [
            sa.Column("memory_revision_id", sa.UUID(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("from_sequence", sa.BigInteger(), nullable=False),
            sa.Column("through_sequence", sa.BigInteger(), nullable=False),
            sa.Column("processor_version", sa.Text(), nullable=False),
            sa.CheckConstraint(
                "version >= 1 AND from_sequence > 0 AND through_sequence >= from_sequence"
            ),
            sa.CheckConstraint("processor_version = 'extractive-v1'"),
            sa.UniqueConstraint("memory_revision_id", "processor_version"),
            sa.ForeignKeyConstraint(
                ["memory_revision_id", "scope_id"], ["memories.id", "memories.scope_id"]
            ),
        ]
        if name == "snapshots":
            columns += [
                sa.Column("summary_id", sa.UUID(), nullable=False),
                sa.ForeignKeyConstraint(
                    ["summary_id", "scope_id"], ["summaries.id", "summaries.scope_id"]
                ),
            ]
        op.create_table(name, *columns)
    op.create_table(
        "inferences",
        *common(),
        sa.Column("text", sa.Text(), nullable=False),
        sa.CheckConstraint("fact_level = 'inference' AND NOT verified"),
    )
    op.create_table(
        "inference_evidence",
        sa.Column("inference_id", sa.UUID(), primary_key=True),
        sa.Column("memory_revision_id", sa.UUID(), primary_key=True),
        sa.Column("scope_id", sa.UUID(), nullable=False),
        sa.ForeignKeyConstraint(
            ["inference_id", "scope_id"], ["inferences.id", "inferences.scope_id"]
        ),
        sa.ForeignKeyConstraint(
            ["memory_revision_id", "scope_id"], ["memories.id", "memories.scope_id"]
        ),
    )
    op.create_table(
        "checkpoints",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("scope_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("processor", sa.Text(), nullable=False),
        sa.Column("through_sequence", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("schema_version = 1 AND processor = 'memory-layers-v1'"),
        sa.ForeignKeyConstraint(["through_sequence"], ["events.sequence"]),
        sa.UniqueConstraint("scope_id", "processor", "through_sequence"),
    )
    for name, target in (("memory_receipts", "memories"), ("inference_receipts", "inferences")):
        op.create_table(
            name,
            sa.Column("scope_id", sa.UUID(), primary_key=True),
            sa.Column("actor_id", sa.UUID(), primary_key=True),
            sa.Column("idempotency_key", sa.UUID(), primary_key=True),
            sa.Column("input_hash", sa.String(64), nullable=False),
            sa.Column("result_id", sa.UUID(), nullable=False),
            sa.ForeignKeyConstraint(
                ["result_id", "scope_id"], [f"{target}.id", f"{target}.scope_id"]
            ),
        )
    op.execute("""CREATE FUNCTION shiros_immutable_history() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'immutable_history' USING ERRCODE = '23514'; END;
        $$ LANGUAGE plpgsql""")
    for name in (
        "sources",
        "entities",
        "artifacts",
        "relations",
        "memories",
        "events",
        "summaries",
        "snapshots",
        "inferences",
        "inference_evidence",
        "checkpoints",
        "memory_embeddings",
        "memory_receipts",
        "inference_receipts",
    ):
        op.execute(
            f"CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON {name} "
            "FOR EACH ROW EXECUTE FUNCTION shiros_immutable_history()"
        )
    op.execute("""CREATE FUNCTION shiros_revision_chain() RETURNS trigger AS $$
        BEGIN
          IF NEW.revision > 1 AND NOT EXISTS (
            SELECT 1 FROM memories WHERE id = NEW.supersedes_id
            AND revision = NEW.revision - 1 AND entity_id = NEW.entity_id
            AND domain = NEW.domain AND memory_id = NEW.memory_id AND scope_id = NEW.scope_id
          ) THEN RAISE EXCEPTION 'invalid_revision_chain' USING ERRCODE = '23514'; END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql""")
    op.execute(
        "CREATE TRIGGER valid_revision BEFORE INSERT ON memories "
        "FOR EACH ROW EXECUTE FUNCTION shiros_revision_chain()"
    )


def downgrade() -> None:
    for name in (
        "inference_receipts",
        "memory_receipts",
        "checkpoints",
        "inference_evidence",
        "inferences",
        "snapshots",
        "summaries",
        "event_clock",
        "events",
        "memory_embeddings",
        "memories",
        "relations",
        "artifacts",
        "entities",
        "sources",
    ):
        op.drop_table(name)
    op.execute("DROP FUNCTION shiros_revision_chain()")
    op.execute("DROP FUNCTION shiros_immutable_history()")
