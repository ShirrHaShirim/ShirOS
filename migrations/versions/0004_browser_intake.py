"""Distinguish transient browser submissions from server-local file selection."""

from alembic import op

revision = "0004_browser_intake"
down_revision = "0003_trusted_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("review_sources_origin_check", "review_sources", type_="check")
    op.create_check_constraint(
        "review_sources_origin_check",
        "review_sources",
        "origin IN ('manual-local-file','manual-browser-input')",
    )


def downgrade() -> None:
    # Refuse downgrading with browser sources rather than relabel immutable provenance.
    op.drop_constraint("review_sources_origin_check", "review_sources", type_="check")
    op.create_check_constraint(
        "review_sources_origin_check", "review_sources", "origin='manual-local-file'"
    )
