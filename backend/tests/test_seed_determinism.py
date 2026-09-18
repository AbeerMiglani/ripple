"""Reproducibility guarantees for the synthetic seed dataset.

The simulator's claim is determinism: the same graph and the same initial
failure produce the same waves, the same ranked interventions, and the same
verified rerun. That claim used to stop at the front door -- the generator
minted node and edge IDs with ``uuid.uuid4()``, which draws from ``os.urandom``
and ignores the ``random.seed(2026)`` at the top of the file, so regenerating
the seed renamed every asset in the system even though the rest of the payload
was already byte-identical.

These tests pin the fix: IDs are now a pure function of topology, the shipped
fixture is exactly what the generator would produce for those identities, and
every edge reference resolves.
"""

import importlib.util
import json
import uuid
from pathlib import Path

import pytest

from app.services.recommendations import _to_deterministic_uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[2]
GENERATOR_PATH = PROJECT_ROOT / "data" / "scripts" / "generate_synthetic.py"
SEED_NODES_PATH = PROJECT_ROOT / "data" / "seed" / "nodes.geojson"
SEED_EDGES_PATH = PROJECT_ROOT / "data" / "seed" / "edges.json"


def load_generator():
    """Import the generator fresh.

    Each import re-runs the module-level ``random.seed(2026)``, so two loads
    model two independent invocations of the script.
    """
    spec = importlib.util.spec_from_file_location("ripple_generate_synthetic", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def generator():
    assert GENERATOR_PATH.is_file(), f"generator not found at {GENERATOR_PATH}"
    return load_generator()


@pytest.fixture(scope="module")
def seed_nodes():
    with open(SEED_NODES_PATH, encoding="utf-8") as f:
        return json.load(f)["features"]


@pytest.fixture(scope="module")
def seed_edges():
    with open(SEED_EDGES_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# The ID derivation itself
# ---------------------------------------------------------------------------

def test_deterministic_id_is_a_pure_function_of_its_identity(generator):
    """Same identity string in, same UUID out -- across calls and across loads."""
    first = generator.deterministic_uuid4("ripple:node:Grid Substation PS-01")
    second = generator.deterministic_uuid4("ripple:node:Grid Substation PS-01")
    assert first == second

    reloaded = load_generator().deterministic_uuid4("ripple:node:Grid Substation PS-01")
    assert reloaded == first


def test_distinct_identities_get_distinct_ids(generator):
    identities = [
        "ripple:node:Grid Substation PS-01",
        "ripple:node:Grid Substation PS-02",
        "ripple:node:Water Facility WS-01",
        "ripple:edge:power_supply:a:b",
        "ripple:edge:water_supply:a:b",
    ]
    derived = [generator.deterministic_uuid4(i) for i in identities]
    assert len(set(derived)) == len(identities)


def test_deterministic_ids_are_version_4(generator):
    """Guards the API contract that dictated the choice of algorithm.

    ``MitigationRecommendation`` types its node fields as pydantic ``UUID4``,
    which validates ``version == 4``. A uuid5 seed ID would be coerced into an
    unrelated surrogate and the API would report IDs that match no node.
    """
    for identity in ("ripple:node:Hospital MC-01", "ripple:edge:road_link:x:y"):
        parsed = uuid.UUID(generator.deterministic_uuid4(identity))
        assert parsed.version == 4
        assert parsed.variant == uuid.RFC_4122


def test_deterministic_id_matches_recommendations_helper(generator):
    """The generator and the backend must not drift apart.

    ``app.services.recommendations._to_deterministic_uuid4`` performs the same
    derivation for IDs that reach the API. If these two implementations ever
    disagree, a seed ID would be rewritten in flight.
    """
    for identity in (
        "ripple:node:Grid Substation PS-01",
        "ripple:node:Road Junction RJ-100",
        "ripple:edge:depends_on:source:target",
        "not-a-uuid-at-all",
    ):
        assert generator.deterministic_uuid4(identity) == str(_to_deterministic_uuid4(identity))


def test_identity_keys_are_prefixed_per_kind(generator):
    """A node and an edge can never share an identity key."""
    assert generator.node_identity("X").startswith("ripple:node:")
    assert generator.edge_identity("road_link", "a", "b").startswith("ripple:edge:")
    assert generator.node_identity("X") != generator.edge_identity("road_link", "a", "b")


def test_edge_identity_is_direction_and_type_sensitive(generator):
    """Distinct edges must not collapse onto one ID."""
    forward = generator.edge_identity("power_supply", "a", "b")
    reverse = generator.edge_identity("power_supply", "b", "a")
    other_type = generator.edge_identity("water_supply", "a", "b")
    assert len({forward, reverse, other_type}) == 3


# ---------------------------------------------------------------------------
# Repeated generation
# ---------------------------------------------------------------------------

def test_regeneration_produces_identical_nodes_and_edges():
    """Two independent generations agree on everything, IDs included.

    This is the assertion that failed before deterministic IDs: the payloads
    already matched, only the IDs differed.
    """
    first = load_generator()
    first_nodes = first.generate_nodes()
    first_edges = first.generate_edges(first_nodes)

    second = load_generator()
    second_nodes = second.generate_nodes()
    second_edges = second.generate_edges(second_nodes)

    assert first_nodes == second_nodes
    assert first_edges == second_edges

    # Spelled out, so a failure says which half broke.
    assert [n["id"] for n in first_nodes] == [n["id"] for n in second_nodes]
    assert [e["id"] for e in first_edges] == [e["id"] for e in second_edges]
    assert [e["source_id"] for e in first_edges] == [e["source_id"] for e in second_edges]
    assert [e["target_id"] for e in first_edges] == [e["target_id"] for e in second_edges]


def test_generated_graph_passes_its_own_identity_assertions(generator):
    """The generator refuses to emit a fixture whose IDs would collide."""
    nodes = generator.generate_nodes()
    edges = generator.generate_edges(nodes)
    generator.assert_identities_unique(nodes, edges)


def test_generator_rejects_duplicate_node_names(generator):
    """A name collision means two nodes sharing a primary key -- fail loudly."""
    nodes = [
        {"id": "a", "name": "Duplicate"},
        {"id": "b", "name": "Duplicate"},
    ]
    with pytest.raises(AssertionError, match="Node names are not unique"):
        generator.assert_identities_unique(nodes, [])


def test_generator_rejects_duplicate_edge_identity(generator):
    """Mirrors the database's uq_network_edge constraint."""
    nodes = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
    edge = {"id": "e", "edge_type": "road_link", "source_id": "a", "target_id": "b"}
    with pytest.raises(AssertionError, match="not unique"):
        generator.assert_identities_unique(nodes, [edge, dict(edge, id="e2")])


def test_generator_rejects_dangling_edge_references(generator):
    nodes = [{"id": "a", "name": "A"}]
    edge = {"id": "e", "edge_type": "road_link", "source_id": "a", "target_id": "missing"}
    with pytest.raises(AssertionError, match="reference unknown nodes"):
        generator.assert_identities_unique(nodes, [edge])


# ---------------------------------------------------------------------------
# The committed fixture is canonical
# ---------------------------------------------------------------------------

def test_committed_node_ids_match_the_deterministic_function(generator, seed_nodes):
    """Every shipped node ID is recomputable from its name alone."""
    mismatches = [
        feature["properties"]["name"]
        for feature in seed_nodes
        if feature["properties"]["id"]
        != generator.deterministic_uuid4(generator.node_identity(feature["properties"]["name"]))
    ]
    assert not mismatches, f"{len(mismatches)} node ID(s) are not canonical: {mismatches[:5]}"


def test_committed_edge_ids_match_the_deterministic_function(generator, seed_edges):
    """Every shipped edge ID is recomputable from its endpoints and type."""
    mismatches = [
        edge["id"]
        for edge in seed_edges
        if edge["id"]
        != generator.deterministic_uuid4(
            generator.edge_identity(edge["edge_type"], edge["source_id"], edge["target_id"])
        )
    ]
    assert not mismatches, f"{len(mismatches)} edge ID(s) are not canonical: {mismatches[:5]}"


def test_committed_seed_reference_integrity(seed_nodes, seed_edges):
    """Every edge endpoint resolves, and nothing shares an identity."""
    node_ids = [f["properties"]["id"] for f in seed_nodes]
    node_names = [f["properties"]["name"] for f in seed_nodes]

    assert len(set(node_ids)) == len(node_ids), "duplicate node IDs"
    assert len(set(node_names)) == len(node_names), "duplicate node names"

    edge_ids = [e["id"] for e in seed_edges]
    assert len(set(edge_ids)) == len(edge_ids), "duplicate edge IDs"
    assert not set(edge_ids) & set(node_ids), "an edge ID collides with a node ID"

    triples = [(e["edge_type"], e["source_id"], e["target_id"]) for e in seed_edges]
    assert len(set(triples)) == len(triples), "duplicate (edge_type, source, target)"

    known = set(node_ids)
    dangling = [
        e["id"] for e in seed_edges
        if e["source_id"] not in known or e["target_id"] not in known
    ]
    assert not dangling, f"{len(dangling)} edge(s) reference unknown nodes"

    self_loops = [e["id"] for e in seed_edges if e["source_id"] == e["target_id"]]
    assert not self_loops, "self-loops violate ck_edge_distinct_endpoints"


def test_committed_seed_ids_are_all_version_4(seed_nodes, seed_edges):
    """The database columns are UUID, and the API validates version 4."""
    for feature in seed_nodes:
        assert uuid.UUID(feature["properties"]["id"]).version == 4
    for edge in seed_edges:
        for key in ("id", "source_id", "target_id"):
            assert uuid.UUID(edge[key]).version == 4
