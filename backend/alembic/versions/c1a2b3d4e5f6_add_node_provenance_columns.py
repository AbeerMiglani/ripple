"""add node provenance columns

Revision ID: c1a2b3d4e5f6
Revises: bbb3dbb1490c
Create Date: 2026-09-12 11:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c1a2b3d4e5f6'
down_revision: str | None = 'bbb3dbb1490c'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'nodes',
        sa.Column('is_synthetic', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    )
    op.add_column(
        'nodes',
        sa.Column('data_source', sa.String(), nullable=False, server_default='synthetic'),
    )


def downgrade() -> None:
    op.drop_column('nodes', 'data_source')
    op.drop_column('nodes', 'is_synthetic')
