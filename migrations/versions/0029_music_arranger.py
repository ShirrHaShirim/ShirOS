"""Preserve arrangers as independent credits rather than composer aliases."""

from alembic import op

revision = "0029_music_arranger"
down_revision = "0028_music_recording_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE music_relations DROP CONSTRAINT music_relations_role_check")
    op.execute("""ALTER TABLE music_relations ADD CONSTRAINT music_relations_role_check
      CHECK(role IN ('composer','arranger','conductor','performer','singer','ensemble','label',
      'work','recording','reissue_of','parent_work','parent_recording','publisher','member',
      'former_member','chief_conductor','suborganization','collaborator'))""")


def downgrade() -> None:
    raise RuntimeError("Restore a verified backup to preserve arranger credits.")
