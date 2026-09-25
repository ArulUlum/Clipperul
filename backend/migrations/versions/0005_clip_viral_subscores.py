"""add clip hook/flow/value/trend sub-scores

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-11

"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("clips", sa.Column("hook_score", sa.Float, nullable=True))
    op.add_column("clips", sa.Column("flow_score", sa.Float, nullable=True))
    op.add_column("clips", sa.Column("value_score", sa.Float, nullable=True))
    op.add_column("clips", sa.Column("trend_score", sa.Float, nullable=True))


def downgrade() -> None:
    op.drop_column("clips", "trend_score")
    op.drop_column("clips", "value_score")
    op.drop_column("clips", "flow_score")
    op.drop_column("clips", "hook_score")
