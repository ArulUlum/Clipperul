"""auto-clip: upload gap per platform instead of one for the whole run

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-03

"""
import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "auto_clip_runs",
        sa.Column("next_publish_by_platform", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.drop_column("auto_clip_runs", "next_publish_at")


def downgrade() -> None:
    op.add_column("auto_clip_runs", sa.Column("next_publish_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_column("auto_clip_runs", "next_publish_by_platform")
