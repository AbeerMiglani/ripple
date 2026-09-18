"""Contract tests for the simulation API response schema.

Population provenance (``is_population_capped``, ``has_unresolved_overlap``,
``study_area_population_cap``) was previously computed, persisted, and then
silently dropped at the API boundary because the response model bound to the
routes did not declare the fields. Pydantic omits undeclared attributes, so
three UI surfaces rendered conditions that could never be true.

These tests pin the serialized contract rather than the class definition, so a
field removed from the response model fails here even if the ORM still has it.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Lean-environment shims are installed once for the whole suite by
# tests/conftest.py, before this file is collected.
from app.api.simulations import SimulationResponse

UTC = timezone.utc

#: Every field the frontend and demo scripts read off a simulation payload.
REQUIRED_RESPONSE_FIELDS = {
    "id",
    "network_id",
    "status",
    "initial_failures",
    "waves",
    "total_failed",
    "population_affected_estimate",
    "raw_population_affected",
    "study_area_population_cap",
    "is_population_capped",
    "has_unresolved_overlap",
    "cascade_stabilized",
    "global_efficiency_before",
    "global_efficiency_after",
}


class _FakeSimulationRow:
    """Stands in for the SQLAlchemy row that from_attributes reads."""

    def __init__(self, **overrides):
        node_id = uuid.uuid4()
        self.id = uuid.uuid4()
        self.network_id = uuid.uuid4()
        self.status = "completed"
        self.initial_failures = [str(node_id)]
        self.waves = [{"wave": 0, "failed_node_ids": [str(node_id)]}]
        self.total_failed = 1
        self.population_affected_estimate = 65_000
        self.raw_population_affected = 124_439
        self.study_area_population_cap = 65_000
        self.is_population_capped = True
        self.has_unresolved_overlap = True
        self.cascade_stabilized = False
        self.global_efficiency_before = 0.18461
        self.global_efficiency_after = 0.17460
        self.error_message = None
        self.created_at = datetime.now(UTC)
        self.completed_at = datetime.now(UTC)
        for key, value in overrides.items():
            setattr(self, key, value)


def test_response_model_declares_every_field_the_ui_reads():
    declared = set(SimulationResponse.model_fields)
    missing = REQUIRED_RESPONSE_FIELDS - declared
    assert not missing, (
        f"SimulationResponse is missing {sorted(missing)}. Pydantic drops "
        "undeclared attributes, so these never reach the client even though "
        "the simulation runner persists them."
    )


def test_population_provenance_survives_serialization():
    """The values must appear in the serialized payload, not just the class."""
    payload = SimulationResponse.model_validate(_FakeSimulationRow()).model_dump()

    assert payload["is_population_capped"] is True
    assert payload["has_unresolved_overlap"] is True
    assert payload["study_area_population_cap"] == 65_000
    assert payload["population_affected_estimate"] == 65_000
    assert payload["raw_population_affected"] == 124_439


def test_raw_population_exceeds_capped_estimate_when_capped():
    """The uncapped figure is what makes a saturated before/after comparable."""
    payload = SimulationResponse.model_validate(_FakeSimulationRow()).model_dump()
    assert payload["raw_population_affected"] > payload["population_affected_estimate"]


def test_truncated_cascade_is_reported_as_not_stabilized():
    payload = SimulationResponse.model_validate(_FakeSimulationRow()).model_dump()
    assert payload["cascade_stabilized"] is False


def test_legacy_rows_without_raw_population_still_serialize():
    """Rows predating raw_population_affected must not break the endpoint."""
    payload = SimulationResponse.model_validate(
        _FakeSimulationRow(raw_population_affected=None, is_population_capped=False)
    ).model_dump()
    assert payload["raw_population_affected"] is None
    assert payload["population_affected_estimate"] == 65_000


@pytest.mark.parametrize(
    "field, value",
    [
        ("is_population_capped", False),
        ("has_unresolved_overlap", False),
        ("cascade_stabilized", True),
    ],
)
def test_boolean_flags_round_trip_both_states(field, value):
    payload = SimulationResponse.model_validate(
        _FakeSimulationRow(**{field: value})
    ).model_dump()
    assert payload[field] is value
