"""add published_at/external_post_id/external_url to scheduled_posts

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-14

"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scheduled_posts", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("scheduled_posts", sa.Column("external_post_id", sa.String(length=255), nullable=True))
    op.add_column("scheduled_posts", sa.Column("external_url", sa.String(length=1000), nullable=True))


def downgrade() -> None:
    op.drop_column("scheduled_posts", "external_url")
    op.drop_column("scheduled_posts", "external_post_id")
    op.drop_column("scheduled_posts", "published_at")
