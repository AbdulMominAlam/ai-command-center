"""add items.summary

Revision ID: b7c1d2e3f4a5
Revises: 0e6798a26766
Create Date: 2026-10-02 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b7c1d2e3f4a5'
down_revision: Union[str, Sequence[str], None] = '0e6798a26766'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('items', sa.Column('summary', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('items', 'summary')
