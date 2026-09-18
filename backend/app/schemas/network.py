from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import UUID4, BaseModel, ConfigDict, model_validator



class NodeBase(BaseModel):
    id: UUID4
    name: str
    display_name: Optional[str] = None
    node_type: str
    lat: float
    lng: float
    capacity: float
    current_load: float
    failure_threshold: float
    population_served: int
    status: str
    is_synthetic: bool = True
    data_source: str = "synthetic"
    name_source: str = "synthetic"
    data_quality: str = "estimated"

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def set_default_display_name(self):
        if not self.display_name:
            self.display_name = self.name
        return self


class EdgeBase(BaseModel):
    id: UUID4
    source_id: UUID4
    target_id: UUID4
    edge_type: str
    weight: float
    capacity: float
    is_bidirectional: bool

    model_config = ConfigDict(from_attributes=True)


class NetworkBase(BaseModel):
    id: UUID4
    name: str
    description: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CentralityScore(BaseModel):
    node_id: UUID4
    name: str
    display_name: Optional[str] = None
    node_type: str
    is_synthetic: bool = True
    data_source: str = "synthetic"
    name_source: str = "synthetic"
    data_quality: str = "estimated"
    score: float
    rank: int

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def set_default_display_name(self):
        if not self.display_name:
            self.display_name = self.name
        return self
