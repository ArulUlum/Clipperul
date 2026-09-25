"""add manual crop position override to clips

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-11

"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clips", sa.Column("crop_x", sa.Float, nullable=True))
    op.add_column("clips", sa.Column("crop_y", sa.Float, nullable=True))


def downgrade() -> None:
    op.drop_column("clips", "crop_y")
    op.drop_column("clips", "crop_x")
