"""add display_name and provenance columns

Revision ID: d2e3f4a5b6c7
Revises: c1a2b3d4e5f6
Create Date: 2026-09-12 16:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: str | None = 'c1a2b3d4e5f6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add columns to nodes table
    op.add_column('nodes', sa.Column('display_name', sa.String(), nullable=True))
    op.add_column(
        'nodes',
        sa.Column('name_source', sa.String(), nullable=False, server_default='synthetic'),
    )
    op.add_column(
        'nodes',
        sa.Column('data_quality', sa.String(), nullable=False, server_default='verified'),
    )

    # 2. Backfill display_name from existing name column
    op.execute("UPDATE nodes SET display_name = name WHERE display_name IS NULL")
    op.alter_column('nodes', 'display_name', nullable=False)

    # 3. Add population cap fields to simulation_results table
    op.add_column(
        'simulation_results',
        sa.Column('study_area_population_cap', sa.Integer(), nullable=False, server_default='65000'),
    )
    op.add_column(
        'simulation_results',
        sa.Column('is_population_capped', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    op.add_column(
        'simulation_results',
        sa.Column('has_unresolved_overlap', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )


def downgrade() -> None:
    op.drop_column('simulation_results', 'has_unresolved_overlap')
    op.drop_column('simulation_results', 'is_population_capped')
    op.drop_column('simulation_results', 'study_area_population_cap')
    op.drop_column('nodes', 'data_quality')
    op.drop_column('nodes', 'name_source')
    op.drop_column('nodes', 'display_name')
