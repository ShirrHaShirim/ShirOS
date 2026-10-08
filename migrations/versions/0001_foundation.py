"""Enable vectors and record the foundation schema, without premature domain tables."""

import sqlalchemy as sa
from alembic import op

revision = "0001_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "system_metadata",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("schema_version >= 1", name="ck_schema_version_positive"),
    )


def downgrade() -> None:
    op.drop_table("system_metadata")
    # The extension may be shared by future schemas; never drop it implicitly.
