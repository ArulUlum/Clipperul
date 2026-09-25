"""add video.subtitles_enabled

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-10

"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "videos",
        sa.Column("subtitles_enabled", sa.Boolean, nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("videos", "subtitles_enabled")
