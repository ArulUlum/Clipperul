"""add progress (0-100 upload percentage) to scheduled_posts

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-29

"""
import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "scheduled_posts",
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("scheduled_posts", "progress")
