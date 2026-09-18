"""
SQLAlchemy models for the Ripple backend.
Uses GeoAlchemy2 for PostGIS geometries.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.postgres import Base

UTC = getattr(datetime, "UTC", timezone.utc)  # noqa: UP017


class Network(Base):
    __tablename__ = "networks"
    __table_args__ = (UniqueConstraint("name", name="uq_network_name"),)

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    nodes: Mapped[list[Node]] = relationship("Node", back_populates="network", cascade="all, delete-orphan")
    edges: Mapped[list[Edge]] = relationship("Edge", back_populates="network", cascade="all, delete-orphan")
    scenarios: Mapped[list[Scenario]] = relationship("Scenario", back_populates="network")


class Node(Base):
    __tablename__ = "nodes"
    __table_args__ = (
        CheckConstraint("lat >= -90 AND lat <= 90", name="ck_node_latitude"),
        CheckConstraint("lng >= -180 AND lng <= 180", name="ck_node_longitude"),
        CheckConstraint("capacity > 0", name="ck_node_capacity_positive"),
        CheckConstraint("current_load >= 0", name="ck_node_load_nonnegative"),
        CheckConstraint("failure_threshold > 0", name="ck_node_threshold_positive"),
        CheckConstraint("population_served >= 0", name="ck_node_population_nonnegative"),
        CheckConstraint(
            "service_radius_m IS NULL OR service_radius_m > 0",
            name="ck_node_service_radius_positive",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    network_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("networks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    node_type: Mapped[str] = mapped_column(
        Enum(
            "power_substation",
            "water_station",
            "hospital",
            "road_junction",
            "telecom_tower",
            name="node_type_enum",
        ),
        nullable=False,
    )
    # PostGIS geometry (Point, SRID 4326 for WGS84)
    geom: Mapped[Any] = mapped_column(Geometry("POINT", srid=4326), nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lng: Mapped[float] = mapped_column(Float, nullable=False)

    capacity: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    current_load: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    failure_threshold: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    population_served: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Radius of the area this asset actually serves, in metres. Nullable because
    # it is not known for every asset: population impact falls back to an additive
    # sum when it is absent, and deduplicates service areas spatially when it is
    # present (see app.simulation.population).
    service_radius_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("operational", "degraded", "failed", name="node_status_enum"),
        default="operational",
        nullable=False,
    )
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    data_source: Mapped[str] = mapped_column(String, nullable=False, default="synthetic", server_default="synthetic")
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    name_source: Mapped[str] = mapped_column(
        String, nullable=False, default="synthetic", server_default="synthetic"
    )
    # Defaults to "estimated", not "verified": the shipped dataset is synthetic,
    # and this value is rendered to the user in the map tooltip, the criticality
    # panel and the selection chips. Only genuinely observed data may claim
    # "verified" (see data/seed/README.md for the provenance vocabulary).
    data_quality: Mapped[str] = mapped_column(
        String, nullable=False, default="estimated", server_default="estimated"
    )

    @validates("name")
    def sync_display_name_from_name(self, key, value):
        if not getattr(self, "display_name", None):
            self.display_name = value
        return value

    @validates("display_name")
    def validate_display_name(self, key, value):
        if not value and getattr(self, "name", None):
            return self.name
        return value

    network: Mapped[Network] = relationship("Network", back_populates="nodes")
    # Relationships for edges where this node is source/target
    edges_out: Mapped[list[Edge]] = relationship("Edge", foreign_keys="Edge.source_id", back_populates="source")
    edges_in: Mapped[list[Edge]] = relationship("Edge", foreign_keys="Edge.target_id", back_populates="target")


class Edge(Base):
    __tablename__ = "infra_edges"
    __table_args__ = (
        CheckConstraint("source_id <> target_id", name="ck_edge_distinct_endpoints"),
        CheckConstraint("weight >= 0", name="ck_edge_weight_nonnegative"),
        CheckConstraint("capacity > 0", name="ck_edge_capacity_positive"),
        UniqueConstraint("network_id", "source_id", "target_id", "edge_type", name="uq_network_edge"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    network_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("networks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("nodes.id", ondelete="CASCADE"), nullable=False, index=True
    )

    edge_type: Mapped[str] = mapped_column(
        Enum(
            "power_supply",
            "water_supply",
            "road_link",
            "depends_on",
            # Explicit dependency declarations. The supply/link names above carry
            # an implicit dependency of the matching kind; these say so outright,
            # for datasets that model the dependency separately from the delivery.
            "requires_power",
            "requires_water",
            "requires_transit",
            name="edge_type_enum",
        ),
        nullable=False,
    )
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    capacity: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    is_bidirectional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    network: Mapped[Network] = relationship("Network", back_populates="edges")
    source: Mapped[Node] = relationship("Node", foreign_keys=[source_id], back_populates="edges_out")
    target: Mapped[Node] = relationship("Node", foreign_keys=[target_id], back_populates="edges_in")


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    network_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("networks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(String, nullable=True)

    # JSON list of dicts: {"type": "add_edge", "source": "uuid", "target": "uuid", ...}
    modifications: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    # JSON list of initial failed node UUID strings
    initial_failures: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)

    cached_result_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("simulation_results.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    network: Mapped[Network] = relationship("Network", back_populates="scenarios")
    result: Mapped[SimulationResult | None] = relationship("SimulationResult", foreign_keys=[cached_result_id])


class SimulationResult(Base):
    __tablename__ = "simulation_results"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    network_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("networks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        Enum("pending", "running", "completed", "failed", name="sim_status_enum"),
        nullable=False,
        default="pending",
    )
    # JSON list of initial failed node UUID strings
    initial_failures: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)

    # JSON list of dicts: [{"wave": 0, "failed_node_ids": ["uuid"]}, ...]
    waves: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)

    total_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    population_affected_estimate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Uncapped sum. Kept alongside the capped estimate so a before/after
    # comparison stays meaningful when both runs saturate the study-area cap.
    # Nullable because rows written before this column existed have no recorded
    # raw value, and back-filling one would misrepresent those runs.
    raw_population_affected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Overlap-resolved total: each person is counted once even when several
    # failed assets serve them. Nullable because it can only be computed when
    # the nodes carry service geometry, and because rows written before this
    # column existed have no value that could honestly be back-filled.
    deduplicated_population_affected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    study_area_population_cap: Mapped[int] = mapped_column(
        Integer, nullable=False, default=65000, server_default="65000"
    )
    is_population_capped: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    has_unresolved_overlap: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # False when the cascade was truncated at the wave guardrail rather than
    # reaching a fixed point. A truncated run is still a valid bounded result.
    cascade_stabilized: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    global_efficiency_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    global_efficiency_after: Mapped[float | None] = mapped_column(Float, nullable=True)

    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
