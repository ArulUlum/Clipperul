"""clip layout ("full" / "news") per clip, and the layout new clips of a video get

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-10

"""
import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clips", sa.Column("layout", sa.String(20), nullable=False, server_default="full"))
    op.add_column("videos", sa.Column("clip_layout", sa.String(20), nullable=True))


def downgrade() -> None:
    op.drop_column("videos", "clip_layout")
    op.drop_column("clips", "layout")
