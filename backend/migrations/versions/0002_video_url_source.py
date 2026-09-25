"""add video.source_url and DOWNLOADING status

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-09

"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("videos", sa.Column("source_url", sa.String(2000), nullable=True))
    # Postgres 12+ allows ALTER TYPE ... ADD VALUE inside a transaction as long as the new
    # value isn't used in the same transaction — this migration only adds it.
    op.execute("ALTER TYPE video_status ADD VALUE IF NOT EXISTS 'DOWNLOADING' AFTER 'QUEUED'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE — removing an enum value requires rebuilding
    # the type, which isn't worth doing for a dev-schema downgrade. Column drop is safe.
    op.drop_column("videos", "source_url")
