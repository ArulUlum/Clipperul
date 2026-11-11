"""facecam crop for the gaming layout

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-11

"""
import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clips", sa.Column("facecam_x", sa.Float(), nullable=True))
    op.add_column("clips", sa.Column("facecam_y", sa.Float(), nullable=True))
    op.add_column("clips", sa.Column("facecam_size", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("clips", "facecam_size")
    op.drop_column("clips", "facecam_y")
    op.drop_column("clips", "facecam_x")
