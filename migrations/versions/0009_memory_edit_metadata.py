"""Versioned memory labels, structured references and reviewable edit proposals."""

from alembic import op

revision = "0009_memory_edit_metadata"
down_revision = "0008_automatic_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE memory_revision_metadata (
        revision_id uuid PRIMARY KEY REFERENCES memories(id),
        scope_id uuid NOT NULL, title text NOT NULL,
        source_records jsonb NOT NULL DEFAULT '[]'::jsonb,
        CHECK(jsonb_typeof(source_records)='array'),
        CHECK(jsonb_array_length(source_records)<=30)
    )""")
    op.execute("""CREATE TRIGGER immutable_history
        BEFORE UPDATE OR DELETE ON memory_revision_metadata
        FOR EACH ROW EXECUTE FUNCTION shiros_immutable_history()""")
    op.execute("ALTER TABLE memory_candidates ADD COLUMN target_memory_id uuid")
    op.execute("ALTER TABLE memory_candidates ADD COLUMN expected_memory_revision integer")
    op.execute("""ALTER TABLE memory_candidates ADD CONSTRAINT candidate_edit_target CHECK (
        (target_memory_id IS NULL AND expected_memory_revision IS NULL) OR
        (target_memory_id IS NOT NULL AND expected_memory_revision>0))""")
    op.execute("ALTER TABLE candidate_states ADD COLUMN proposed_title text")
    op.execute("ALTER TABLE candidate_states ADD COLUMN proposed_sources jsonb")
    op.drop_constraint("memory_candidates_fact_level_check", "memory_candidates", type_="check")
    op.create_check_constraint(
        "memory_candidates_fact_level_check",
        "memory_candidates",
        "fact_level IN ('fact','explicit_statement','derived_fact','pattern',"
        "'inference','hypothesis','model_guess')",
    )


def downgrade() -> None:
    raise RuntimeError("Memory metadata and edit history require a forward migration.")
