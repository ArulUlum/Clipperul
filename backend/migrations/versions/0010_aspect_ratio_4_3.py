"""add 4:3 aspect ratio value

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-11

"""
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE aspect_ratio ADD VALUE IF NOT EXISTS '4:3' AFTER '3:4'")


def downgrade() -> None:
    # Postgres doesn't support removing an enum value — not worth a table-rebuild for a
    # dev-schema downgrade path (same tradeoff as the other enum-extension migrations).
    pass
