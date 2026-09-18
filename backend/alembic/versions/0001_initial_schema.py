"""Create the Ripple canonical schema.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-09-10
"""

import sqlalchemy as sa
from geoalchemy2 import Geometry
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


# postgresql.ENUM (not the cross-dialect sa.Enum) below, with create_type=False
# on every one: upgrade()/downgrade() create and drop these types explicitly
# (see the loops further down). Without create_type=False, SQLAlchemy *also*
# tries to auto-create each type when the table that uses it is created
# (op.create_table's before_create DDL event, which Alembic runs with
# checkfirst=False) — a second CREATE TYPE for a type the explicit loop
# already created, which Postgres rejects as a duplicate; symmetrically for
# DROP TYPE on downgrade. create_type is a postgresql.ENUM-only constructor
# parameter — passing it to sa.Enum is silently ignored (no attribute is even
# set), so the dialect-native type is required for this to have any effect.
node_type = postgresql.ENUM(
    "power_substation",
    "water_station",
    "hospital",
    "road_junction",
    "telecom_tower",
    name="node_type_enum",
    create_type=False,
)
node_status = postgresql.ENUM(
    "operational", "degraded", "failed", name="node_status_enum", create_type=False
)
edge_type = postgresql.ENUM(
    "power_supply", "water_supply", "road_link", "depends_on", name="edge_type_enum", create_type=False
)
simulation_status = postgresql.ENUM(
    "pending", "running", "completed", "failed", name="sim_status_enum", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    for enum_type in (node_type, node_status, edge_type, simulation_status):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "networks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("name", name="uq_network_name"),
    )
    op.create_table(
        "nodes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("node_type", node_type, nullable=False),
        sa.Column("geom", Geometry("POINT", srid=4326), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lng", sa.Float(), nullable=False),
        sa.Column("capacity", sa.Float(), nullable=False, server_default="100.0"),
        sa.Column("current_load", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("failure_threshold", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("population_served", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", node_status, nullable=False, server_default="operational"),
        sa.CheckConstraint("lat >= -90 AND lat <= 90", name="ck_node_latitude"),
        sa.CheckConstraint("lng >= -180 AND lng <= 180", name="ck_node_longitude"),
        sa.CheckConstraint("capacity > 0", name="ck_node_capacity_positive"),
        sa.CheckConstraint("current_load >= 0", name="ck_node_load_nonnegative"),
        sa.CheckConstraint("failure_threshold > 0", name="ck_node_threshold_positive"),
        sa.CheckConstraint("population_served >= 0", name="ck_node_population_nonnegative"),
        sa.ForeignKeyConstraint(["network_id"], ["networks.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_nodes_network_id", "nodes", ["network_id"])
    op.create_table(
        "infra_edges",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("edge_type", edge_type, nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column("capacity", sa.Float(), nullable=False, server_default="100.0"),
        sa.Column("is_bidirectional", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.CheckConstraint("source_id <> target_id", name="ck_edge_distinct_endpoints"),
        sa.CheckConstraint("weight >= 0", name="ck_edge_weight_nonnegative"),
        sa.CheckConstraint("capacity > 0", name="ck_edge_capacity_positive"),
        sa.UniqueConstraint("network_id", "source_id", "target_id", "edge_type", name="uq_network_edge"),
        sa.ForeignKeyConstraint(["network_id"], ["networks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_id"], ["nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_id"], ["nodes.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_infra_edges_network_id", "infra_edges", ["network_id"])
    op.create_index("ix_infra_edges_source_id", "infra_edges", ["source_id"])
    op.create_index("ix_infra_edges_target_id", "infra_edges", ["target_id"])
    op.create_table(
        "simulation_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", simulation_status, nullable=False, server_default="pending"),
        sa.Column("initial_failures", sa.JSON(), nullable=False),
        sa.Column("waves", sa.JSON(), nullable=False),
        sa.Column("total_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("population_affected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("global_efficiency_before", sa.Float(), nullable=True),
        sa.Column("global_efficiency_after", sa.Float(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["network_id"], ["networks.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_simulation_results_network_id", "simulation_results", ["network_id"])
    op.create_index("ix_simulation_results_created_at", "simulation_results", ["created_at"])
    op.create_table(
        "scenarios",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("network_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("modifications", sa.JSON(), nullable=False),
        sa.Column("initial_failures", sa.JSON(), nullable=False),
        sa.Column("cached_result_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["network_id"], ["networks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["cached_result_id"], ["simulation_results.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_scenarios_network_id", "scenarios", ["network_id"])


def downgrade() -> None:
    op.drop_index("ix_scenarios_network_id", table_name="scenarios")
    op.drop_table("scenarios")
    op.drop_index("ix_simulation_results_created_at", table_name="simulation_results")
    op.drop_index("ix_simulation_results_network_id", table_name="simulation_results")
    op.drop_table("simulation_results")
    op.drop_index("ix_infra_edges_target_id", table_name="infra_edges")
    op.drop_index("ix_infra_edges_source_id", table_name="infra_edges")
    op.drop_index("ix_infra_edges_network_id", table_name="infra_edges")
    op.drop_table("infra_edges")
    op.drop_index("ix_nodes_network_id", table_name="nodes")
    op.drop_table("nodes")
    op.drop_table("networks")
    bind = op.get_bind()
    for enum_type in (simulation_status, edge_type, node_status, node_type):
        enum_type.drop(bind, checkfirst=True)
