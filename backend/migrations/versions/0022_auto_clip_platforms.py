"""auto-clip: which platforms a run posts to

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-13

"""
import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("auto_clip_settings", sa.Column("platforms", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("auto_clip_settings", "platforms")
