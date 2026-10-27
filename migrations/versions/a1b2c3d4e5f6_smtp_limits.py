"""smtp send limits

Revision ID: a1b2c3d4e5f6
Revises: 1bfcd5b33afd
Create Date: 2026-10-27 00:00:00.000000+00:00

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '1bfcd5b33afd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('smtp', sa.Column('send_limit', sa.INTEGER(), server_default='0', nullable=False))
    op.add_column('smtp', sa.Column('sent_count', sa.INTEGER(), server_default='0', nullable=False))


def downgrade() -> None:
    op.drop_column('smtp', 'sent_count')
    op.drop_column('smtp', 'send_limit')
