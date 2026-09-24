"""link each simulation run to the scenario it applied

Revision ID: a4c8e1f2b9d3
Revises: f3b7c2e9a10d
Create Date: 2026-09-24

Adds ``simulation_results.scenario_id``. Until now a run did not record which
scenario it applied; the only link ran the other way, through
``scenarios.cached_result_id``, which names a scenario's *latest* run only.
Recommendations for a scenario run were therefore computed against the
unmodified network, producing negative or zero deltas and payloads that
silently dropped the modification already applied.

Existing runs are back-filled from ``cached_result_id`` -- the one link that
exists for them. Runs a scenario's cache has since moved past cannot be
recovered and stay NULL, which the application reads as "unmodified network",
the same answer it gave before this revision.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4c8e1f2b9d3"
down_revision: str | None = "f3b7c2e9a10d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK_NAME = "fk_simulation_results_scenario_id"
INDEX_NAME = "ix_simulation_results_scenario_id"


def upgrade() -> None:
    op.add_column(
        "simulation_results",
        sa.Column("scenario_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        FK_NAME,
        "simulation_results",
        "scenarios",
        ["scenario_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(INDEX_NAME, "simulation_results", ["scenario_id"])
    op.execute(
        """
        UPDATE simulation_results AS sr
        SET scenario_id = s.id
        FROM scenarios AS s
        WHERE s.cached_result_id = sr.id
        """
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="simulation_results")
    op.drop_constraint(FK_NAME, "simulation_results", type_="foreignkey")
    op.drop_column("simulation_results", "scenario_id")
