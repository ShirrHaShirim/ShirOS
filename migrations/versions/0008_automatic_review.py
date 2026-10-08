"""Keep automated policy decisions distinguishable from human review."""

from alembic import op

revision = "0008_automatic_review"
down_revision = "0007_delegated_edits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("candidate_states_reason_code_check", "candidate_states", type_="check")
    op.create_check_constraint(
        "candidate_states_reason_code_check",
        "candidate_states",
        "reason_code IN ('manual_stage','manual_edit','manual_approve','manual_reject',"
        "'source_revoked','model_proposal','auto_approve_ordinary_v1',"
        "'requires_sensitive_review','requires_uncertain_review')",
    )


def downgrade() -> None:
    raise RuntimeError("Automatic review audit history requires a forward migration.")
