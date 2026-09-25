"""add 3:4 aspect ratio value

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-11

"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE aspect_ratio ADD VALUE IF NOT EXISTS '3:4' AFTER '1:1'")


def downgrade() -> None:
    # Postgres doesn't support removing an enum value — not worth a table-rebuild for a
    # dev-schema downgrade path (same tradeoff as the other enum-extension migrations).
    pass
