"""video.category nullable + DETECTING_CATEGORY status

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-10

"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("videos", "category", nullable=True)
    op.execute("ALTER TYPE video_status ADD VALUE IF NOT EXISTS 'DETECTING_CATEGORY' AFTER 'TRANSCRIBING'")


def downgrade() -> None:
    # Any NULL categories from auto-detected videos would violate the NOT NULL constraint on
    # downgrade — not worth backfilling for a dev-schema downgrade path.
    op.alter_column("videos", "category", nullable=False)
