"""add custom font size / position to clip renders

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-11

"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clip_renders", sa.Column("font_size", sa.Integer, nullable=True))
    op.add_column("clip_renders", sa.Column("position_x", sa.Float, nullable=True))
    op.add_column("clip_renders", sa.Column("position_y", sa.Float, nullable=True))


def downgrade() -> None:
    op.drop_column("clip_renders", "position_y")
    op.drop_column("clip_renders", "position_x")
    op.drop_column("clip_renders", "font_size")
