"""add tasks.priority_set_by_user

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-06 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing rows get false: no priority so far counts as set by you.
    op.add_column('tasks', sa.Column('priority_set_by_user', sa.Boolean(),
                                     server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('tasks', 'priority_set_by_user')
