"""promote subtitle preset/font/position to clip-level current settings

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-11

"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clips", sa.Column("subtitle_preset_id", sa.Uuid(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_clips_subtitle_preset_id", "clips", "subtitle_presets", ["subtitle_preset_id"], ["id"], ondelete="SET NULL"
    )
    op.add_column("clips", sa.Column("subtitle_font_size", sa.Integer, nullable=True))
    op.add_column("clips", sa.Column("subtitle_position_x", sa.Float, nullable=True))
    op.add_column("clips", sa.Column("subtitle_position_y", sa.Float, nullable=True))


def downgrade() -> None:
    op.drop_column("clips", "subtitle_position_y")
    op.drop_column("clips", "subtitle_position_x")
    op.drop_column("clips", "subtitle_font_size")
    op.drop_constraint("fk_clips_subtitle_preset_id", "clips", type_="foreignkey")
    op.drop_column("clips", "subtitle_preset_id")
