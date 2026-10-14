"""Allow the white appearance preference without changing stored selections."""

from alembic import op

revision = "0015_white_theme"
down_revision = "0014_knowledge_layers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE workspace_preferences DROP CONSTRAINT workspace_preferences_theme_check"
    )
    op.execute("""ALTER TABLE workspace_preferences ADD CONSTRAINT workspace_preferences_theme_check
        CHECK(theme IN ('white','ivory','forest','lapis','plum','graphite'))""")


def downgrade() -> None:
    op.execute("UPDATE workspace_preferences SET theme='ivory' WHERE theme='white'")
    op.execute(
        "ALTER TABLE workspace_preferences DROP CONSTRAINT workspace_preferences_theme_check"
    )
    op.execute("""ALTER TABLE workspace_preferences ADD CONSTRAINT workspace_preferences_theme_check
        CHECK(theme IN ('ivory','forest','lapis','plum','graphite'))""")
