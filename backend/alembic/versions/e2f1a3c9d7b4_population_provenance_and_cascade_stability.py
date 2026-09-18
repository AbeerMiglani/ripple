"""add raw population, cascade stability, and honest synthetic data_quality default

Revision ID: e2f1a3c9d7b4
Revises: d2e3f4a5b6c7
Create Date: 2026-09-13 15:30:00.000000

Three related corrections:

1. ``simulation_results.raw_population_affected`` stores the uncapped population
   sum alongside the study-area-capped ``population_affected_estimate``. Without
   it, a baseline and an intervention that both exceed the cap report an
   identical figure, so a real improvement is invisible in before/after views.
   The column is nullable on purpose: rows written before this migration have no
   recorded raw value, and inventing one would misrepresent historical runs.

2. ``simulation_results.cascade_stabilized`` records whether the cascade reached
   a natural fixed point or was truncated at the wave guardrail. Historical rows
   default to ``true`` because a truncated cascade previously raised and was
   persisted as ``failed``, never as a completed result.

3. ``nodes.data_quality`` now defaults to ``'estimated'`` rather than
   ``'verified'``. The seed dataset is entirely synthetic, so labelling it
   "verified" overstated its provenance in the map tooltip, criticality panel
   and selection chips. The backfill is restricted to ``is_synthetic = true`` so
   any genuinely verified record keeps its label.

NOTE FOR EXISTING DATABASES: revision ``c4f2a7d9e1b1`` was an abandoned branch
and has been removed. It was never reachable through ``alembic upgrade head``
(two heads made that command fail), but a database stamped at it directly must
be repaired before upgrading::

    ALTER TABLE nodes DROP COLUMN IF EXISTS name_source;
    ALTER TABLE nodes DROP COLUMN IF EXISTS data_quality;
    alembic stamp bbb3dbb1490c
    alembic upgrade head

Its other columns are nullable or carry server defaults, so leaving them in
place is harmless.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e2f1a3c9d7b4'
down_revision: str | None = 'd2e3f4a5b6c7'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Uncapped population total, so capped before/after comparisons stay honest.
    op.add_column(
        'simulation_results',
        sa.Column('raw_population_affected', sa.Integer(), nullable=True),
    )

    # 2. Whether the cascade settled on its own or hit the wave guardrail.
    op.add_column(
        'simulation_results',
        sa.Column(
            'cascade_stabilized',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('true'),
        ),
    )

    # 3. Synthetic data must not present itself as verified.
    op.alter_column('nodes', 'data_quality', server_default='estimated')
    op.execute(
        "UPDATE nodes SET data_quality = 'estimated' "
        "WHERE is_synthetic = true AND data_quality = 'verified'"
    )


def downgrade() -> None:
    op.alter_column('nodes', 'data_quality', server_default='verified')
    op.execute(
        "UPDATE nodes SET data_quality = 'verified' "
        "WHERE is_synthetic = true AND data_quality = 'estimated'"
    )
    op.drop_column('simulation_results', 'cascade_stabilized')
    op.drop_column('simulation_results', 'raw_population_affected')
