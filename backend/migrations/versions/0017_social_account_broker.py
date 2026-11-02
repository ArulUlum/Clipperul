"""social accounts: broker (Zernio) + optional access token

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-03

"""
import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("social_accounts", sa.Column("broker", sa.String(length=20), nullable=True))
    op.alter_column("social_accounts", "encrypted_access_token", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    # Brokered accounts have no token of their own, so they can't exist in the old shape.
    op.execute("DELETE FROM social_accounts WHERE broker IS NOT NULL")
    op.alter_column("social_accounts", "encrypted_access_token", existing_type=sa.Text(), nullable=False)
    op.drop_column("social_accounts", "broker")
