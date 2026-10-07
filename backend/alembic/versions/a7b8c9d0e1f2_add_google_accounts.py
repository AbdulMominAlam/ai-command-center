"""google_accounts: several Google accounts per user

Moves each oauth_tokens row into google_accounts (with the user's email, since
every token so far came from signing in with that user's own account), adds
account_id to sync_state and items, and points existing Gmail and Calendar rows
at that account. SUCourse rows keep account_id NULL.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-07 17:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

GOOGLE_SOURCES = "('gmail', 'calendar')"


def upgrade() -> None:
    op.create_table(
        "google_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=False),
        sa.Column("scopes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.execute("""
        INSERT INTO google_accounts (user_id, email, encrypted_refresh_token, scopes, created_at, updated_at)
        SELECT t.user_id, u.email, t.encrypted_refresh_token, t.scopes, u.created_at, t.updated_at
        FROM oauth_tokens t JOIN users u ON u.id = t.user_id
        WHERE t.provider = 'google'
    """)

    for table in ("sync_state", "items"):
        op.add_column(table, sa.Column(
            "account_id", sa.Integer(), sa.ForeignKey("google_accounts.id", ondelete="CASCADE")))
        op.execute(f"""
            UPDATE {table} SET account_id = a.id
            FROM google_accounts a
            WHERE a.user_id = {table}.user_id AND {table}.source IN {GOOGLE_SOURCES}
        """)

    op.drop_constraint("sync_state_user_id_source_key", "sync_state", type_="unique")
    op.create_unique_constraint("sync_state_user_id_source_account_id_key", "sync_state",
                                ["user_id", "source", "account_id"], postgresql_nulls_not_distinct=True)
    op.drop_constraint("items_user_id_source_external_id_key", "items", type_="unique")
    op.create_unique_constraint("items_user_id_source_account_id_external_id_key", "items",
                                ["user_id", "source", "account_id", "external_id"],
                                postgresql_nulls_not_distinct=True)

    op.drop_table("oauth_tokens")


def downgrade() -> None:
    # Keeps each user's oldest account; rows of other accounts are deleted,
    # since the old schema can't tell accounts apart.
    op.create_table(
        "oauth_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("encrypted_refresh_token", sa.Text(), nullable=False),
        sa.Column("scopes", sa.Text()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "provider"),
    )
    op.execute("""
        INSERT INTO oauth_tokens (user_id, provider, encrypted_refresh_token, scopes, updated_at)
        SELECT DISTINCT ON (user_id) user_id, 'google', encrypted_refresh_token, scopes, updated_at
        FROM google_accounts ORDER BY user_id, id
    """)
    for table in ("sync_state", "items"):
        op.execute(f"""
            DELETE FROM {table} WHERE account_id IS NOT NULL AND account_id NOT IN
                (SELECT DISTINCT ON (user_id) id FROM google_accounts ORDER BY user_id, id)
        """)

    op.drop_constraint("items_user_id_source_account_id_external_id_key", "items", type_="unique")
    op.create_unique_constraint("items_user_id_source_external_id_key", "items", ["user_id", "source", "external_id"])
    op.drop_constraint("sync_state_user_id_source_account_id_key", "sync_state", type_="unique")
    op.create_unique_constraint("sync_state_user_id_source_key", "sync_state", ["user_id", "source"])
    op.drop_column("items", "account_id")
    op.drop_column("sync_state", "account_id")
    op.drop_table("google_accounts")
