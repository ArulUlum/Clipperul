"""unique constraint on social_accounts(user_id, provider, external_account_id)

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-12

"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_social_accounts_user_provider_external_id",
        "social_accounts",
        ["user_id", "provider", "external_account_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_social_accounts_user_provider_external_id", "social_accounts", type_="unique")
