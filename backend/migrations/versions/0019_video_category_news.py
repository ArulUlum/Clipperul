"""add the news video category

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-06

"""
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE video_category ADD VALUE IF NOT EXISTS 'news' AFTER 'podcast'")


def downgrade() -> None:
    # Postgres doesn't support removing an enum value — not worth a table-rebuild for a
    # dev-schema downgrade path (same tradeoff as the other enum-extension migrations).
    pass
