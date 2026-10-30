"""auto-clip: next_publish_at (spacing between a run's uploads)

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-30

"""
import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("auto_clip_runs", sa.Column("next_publish_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("auto_clip_runs", "next_publish_at")
