"""Least-privilege model proposals and immutable request receipts."""

from alembic import op

revision = "0005_agent_candidates"
down_revision = "0004_browser_intake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table, column, expression in (
        ("local_grants", "role", "role IN ('owner','reviewer','reader','worker','proposer')"),
        (
            "review_sources",
            "origin",
            "origin IN ('manual-local-file','manual-browser-input','model-proposal')",
        ),
        ("memory_candidates", "fact_level", "fact_level IN ('explicit_statement','model_guess')"),
        (
            "candidate_states",
            "reason_code",
            "reason_code IN ('manual_stage','manual_edit','manual_approve','manual_reject',"
            "'source_revoked','model_proposal')",
        ),
    ):
        name = f"{table}_{column}_check"
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, expression)
    op.execute("""CREATE TABLE proposal_receipts (
        actor_id uuid NOT NULL REFERENCES local_identities(id),
        scope_id uuid NOT NULL, request_id uuid NOT NULL,
        candidate_id uuid NOT NULL,
        payload_hash char(64) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(actor_id,scope_id,request_id),
        FOREIGN KEY(candidate_id,scope_id) REFERENCES memory_candidates(id,scope_id)
    )""")
    op.execute(
        "CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE ON proposal_receipts "
        "FOR EACH ROW EXECUTE FUNCTION shiros_immutable_history()"
    )


def downgrade() -> None:
    # Existing model provenance and grants must never be silently relabeled or deleted.
    for table, column, expression in (
        ("local_grants", "role", "role IN ('owner','reviewer','reader','worker')"),
        ("review_sources", "origin", "origin IN ('manual-local-file','manual-browser-input')"),
        ("memory_candidates", "fact_level", "fact_level='explicit_statement'"),
        (
            "candidate_states",
            "reason_code",
            "reason_code IN ('manual_stage','manual_edit','manual_approve','manual_reject',"
            "'source_revoked')",
        ),
    ):
        name = f"{table}_{column}_check"
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, expression)
    op.drop_table("proposal_receipts")
