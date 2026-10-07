"""add cache token counts to llm_usage

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-07 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("llm_usage", sa.Column("cache_creation_input_tokens", sa.Integer(), server_default="0", nullable=False))
    op.add_column("llm_usage", sa.Column("cache_read_input_tokens", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("llm_usage", "cache_read_input_tokens")
    op.drop_column("llm_usage", "cache_creation_input_tokens")
