"""auto-clip: configurable wait between uploads

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-13

"""
import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The server defaults keep every existing user on the original behavior: random, 60-90 minutes.
    op.add_column("auto_clip_settings", sa.Column("gap_mode", sa.String(10), nullable=False, server_default="random"))
    op.add_column("auto_clip_settings", sa.Column("gap_unit", sa.String(10), nullable=False, server_default="minute"))
    op.add_column("auto_clip_settings", sa.Column("gap_min", sa.Integer(), nullable=False, server_default="60"))
    op.add_column("auto_clip_settings", sa.Column("gap_max", sa.Integer(), nullable=False, server_default="90"))


def downgrade() -> None:
    for column in ("gap_max", "gap_min", "gap_unit", "gap_mode"):
        op.drop_column("auto_clip_settings", column)
