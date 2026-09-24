"""Wire schemas for simulation runs.

This module is the single definition of the simulation request/response
contract. ``app.api.simulations`` re-exports these names rather than declaring
its own: the two used to be separate, and had already drifted apart by two
fields -- the live inline copy carried ``raw_population_affected`` and
``cascade_stabilized`` while this one did not, and this one typed ``waves`` as
``list[Any]`` where the live one validated it.

Coordinates do not appear here. Waves are pure ID lists, and a node's position
travels on ``app.schemas.network.NodeBase`` as two named scalars (``lat`` and
``lng``) precisely so no ordering can be got wrong. Wherever a coordinate does
become a positional pair it is GeoJSON ``[longitude, latitude]`` -- see
``app.services.geo``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator

from app.config import settings

#: Kept in sync with app.simulation.population.STUDY_AREA_POPULATION_CAP and the
#: model's server_default.
DEFAULT_STUDY_AREA_CAP = 65_000


class PopulationImpactResult(BaseModel):
    """The full population picture for one run.

    ``raw_population_affected`` is the naive additive sum and double counts
    overlapping service areas; ``deduplicated_population_affected`` resolves
    that overlap spatially and is None when the assets carry no service
    geometry. Both are reported so a run can still be compared against one
    recorded before deduplication existed.
    """

    raw_population_affected: int
    deduplicated_population_affected: Optional[int] = None
    overlap_population: Optional[int] = None
    dedup_method: str = "additive"
    population_affected_estimate: int
    study_area_population_cap: int = DEFAULT_STUDY_AREA_CAP
    is_population_capped: bool = False
    has_unresolved_overlap: bool = False


class WaveSchema(BaseModel):
    """One cascade wave.

    ``failed_node_ids`` is the MARGINAL set -- the assets that newly failed in
    this wave, not the running total. The explicit fields below say so outright
    so consumers stop re-accumulating the lists themselves and stop mistaking
    one quantity for the other. They are optional because results persisted
    before they existed carry only ``failed_node_ids``, which was already
    marginal.
    """

    wave: int
    failed_node_ids: list[UUID4]
    marginal_failed_node_ids: Optional[list[UUID4]] = None
    cumulative_failed_node_ids: Optional[list[UUID4]] = None


class SimulationCreate(BaseModel):
    network_id: UUID4
    initial_failures: list[UUID4] = Field(min_length=1, max_length=settings.max_initial_failures)
    scenario_id: Optional[UUID4] = None

    @field_validator("initial_failures")
    @classmethod
    def initial_failures_must_be_unique(cls, values: list[UUID4]) -> list[UUID4]:
        if len(set(values)) != len(values):
            raise ValueError("initial_failures must not contain duplicates")
        return values


class SimulationResponse(BaseModel):
    id: UUID4
    network_id: UUID4
    status: str
    initial_failures: list[UUID4]
    waves: list[WaveSchema] = Field(default_factory=list)
    total_failed: int = 0
    population_affected_estimate: int = 0
    # Population provenance must reach the client: without these the UI cannot
    # tell the user that a figure was capped, nor show a meaningful before/after
    # when both runs saturate the cap. raw_population_affected is optional
    # because rows predating the column have no recorded raw value, and
    # deduplicated_population_affected because it can only be computed when the
    # assets carry service geometry.
    raw_population_affected: Optional[int] = None
    deduplicated_population_affected: Optional[int] = None
    study_area_population_cap: int = DEFAULT_STUDY_AREA_CAP
    is_population_capped: bool = False
    has_unresolved_overlap: bool = False
    cascade_stabilized: bool = True
    global_efficiency_before: Optional[float] = None
    global_efficiency_after: Optional[float] = None
    error_message: Optional[str] = None
    created_at: datetime
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)
