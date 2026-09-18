"""service areas, deduplicated population, and explicit dependency edge types

Revision ID: f3b7c2e9a10d
Revises: e2f1a3c9d7b4
Create Date: 2026-09-14

Three related additions:

1. ``nodes.service_radius_m`` -- the radius of the area an asset actually
   serves. Population impact previously summed ``population_served`` across
   every failed asset, which double counts anyone served by two of them and
   routinely exceeded the whole study area's census. With a radius the service
   areas can be unioned geometrically and each person counted once.

2. ``simulation_results.deduplicated_population_affected`` -- the overlap
   resolved total, stored alongside the raw sum rather than replacing it so a
   run can still be compared against one recorded before this existed.

3. Three additional ``edge_type_enum`` values. The cascade now distinguishes
   what a link delivers, so a blocked road stops de-energising an electrical
   tower; these let a dataset declare a dependency explicitly rather than
   having it inferred from a supply link.

Both new columns are nullable. Rows that predate them have no value that could
be back-filled honestly, and the application treats "absent" as a first-class
case (population impact falls back to the additive sum).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f3b7c2e9a10d"
down_revision: str | None = "e2f1a3c9d7b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Values added to edge_type_enum by this revision.
NEW_EDGE_TYPES = ("requires_power", "requires_water", "requires_transit")

#: The enum as it stood before this revision, needed to rebuild it on downgrade.
PREVIOUS_EDGE_TYPES = ("power_supply", "water_supply", "road_link", "depends_on")


def upgrade() -> None:
    op.add_column("nodes", sa.Column("service_radius_m", sa.Float(), nullable=True))
    op.create_check_constraint(
        "ck_node_service_radius_positive",
        "nodes",
        "service_radius_m IS NULL OR service_radius_m > 0",
    )
    op.add_column(
        "simulation_results",
        sa.Column("deduplicated_population_affected", sa.Integer(), nullable=True),
    )

    # ALTER TYPE ... ADD VALUE cannot be followed by a use of the new value in
    # the same transaction, so it is issued in its own autocommit block. This
    # is a no-op on a database that already has the value.
    with op.get_context().autocommit_block():
        for value in NEW_EDGE_TYPES:
            op.execute(f"ALTER TYPE edge_type_enum ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Postgres cannot drop a single enum value, so the type is rebuilt without
    # the new ones. Any edge actually using a removed value is rewritten to the
    # closest surviving equivalent first, otherwise the cast below fails.
    op.execute(
        "UPDATE infra_edges SET edge_type = 'power_supply' "
        "WHERE edge_type = 'requires_power'"
    )
    op.execute(
        "UPDATE infra_edges SET edge_type = 'water_supply' "
        "WHERE edge_type = 'requires_water'"
    )
    op.execute(
        "UPDATE infra_edges SET edge_type = 'road_link' "
        "WHERE edge_type = 'requires_transit'"
    )

    previous = ", ".join(f"'{value}'" for value in PREVIOUS_EDGE_TYPES)
    op.execute("ALTER TYPE edge_type_enum RENAME TO edge_type_enum_old")
    op.execute(f"CREATE TYPE edge_type_enum AS ENUM ({previous})")
    op.execute(
        "ALTER TABLE infra_edges ALTER COLUMN edge_type "
        "TYPE edge_type_enum USING edge_type::text::edge_type_enum"
    )
    op.execute("DROP TYPE edge_type_enum_old")

    op.drop_column("simulation_results", "deduplicated_population_affected")
    op.drop_constraint("ck_node_service_radius_positive", "nodes", type_="check")
    op.drop_column("nodes", "service_radius_m")
