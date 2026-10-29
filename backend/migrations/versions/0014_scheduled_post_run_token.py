"""add run_token to scheduled_posts (identifies the one publish attempt allowed to run)

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-29

"""
import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("scheduled_posts", sa.Column("run_token", sa.String(length=36), nullable=True))


def downgrade() -> None:
    op.drop_column("scheduled_posts", "run_token")
