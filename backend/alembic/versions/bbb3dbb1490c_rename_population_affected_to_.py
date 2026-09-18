"""rename population_affected to population_affected_estimate

Revision ID: bbb3dbb1490c
Revises: 0001_initial_schema
Create Date: 2026-09-10 19:53:32.417387

"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'bbb3dbb1490c'
down_revision: str | None = '0001_initial_schema'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column('simulation_results', 'population_affected', new_column_name='population_affected_estimate')


def downgrade() -> None:
    op.alter_column('simulation_results', 'population_affected_estimate', new_column_name='population_affected')
