"""Domain-specific edge semantics.

A generic dependency graph fails a node whenever anything upstream of it dies.
Real infrastructure does not work that way: a blocked road does not de-energise
an electrical tower, but a hospital that loses either its power or its water is
offline either way.
"""

from __future__ import annotations

import networkx as nx

from app.simulation.cascade import run_cascade
from app.simulation.semantics import edge_services, required_services


def _asset(G, node_id, node_type, *, load=10.0, capacity=1_000.0):
    G.add_node(
        node_id,
        node_type=node_type,
        current_load=load,
        capacity=capacity,
        failure_threshold=1.0,
        population_served=0,
    )


def _failed(waves):
    out = set()
    for w in waves:
        out.update(w["failed_node_ids"])
    return out


# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

def test_supply_and_requires_spellings_name_the_same_service():
    assert edge_services({"edge_type": "power_supply"}) == {"power"}
    assert edge_services({"edge_type": "requires_power"}) == {"power"}
    assert edge_services({"edge_type": "road_link"}) == {"transit"}


def test_generic_depends_on_names_no_specific_service():
    """It cannot be a *critical* dependency of any particular kind."""
    assert edge_services({"edge_type": "depends_on"}) == set()


def test_parallel_typed_links_are_both_visible():
    """A DiGraph holds one edge per pair; the builder merges types into a set."""
    assert edge_services({"edge_types": {"power_supply", "water_supply"}}) == {"power", "water"}


def test_hospital_requires_both_power_and_water():
    assert required_services({"node_type": "hospital"}) == frozenset({"power", "water"})
    assert required_services({"node_type": "power_substation"}) == frozenset({"power"})


# ---------------------------------------------------------------------------
# Propagation
# ---------------------------------------------------------------------------

def test_blocked_road_does_not_fail_an_electrical_tower():
    """The headline case. A telecom tower depends on power, not transit."""
    G = nx.DiGraph()
    _asset(G, "JUNCTION", "road_junction")
    _asset(G, "TOWER", "telecom_tower")
    _asset(G, "GRID", "power_substation")
    G.add_edge("JUNCTION", "TOWER", edge_type="road_link", capacity=100.0)
    G.add_edge("GRID", "TOWER", edge_type="power_supply", capacity=100.0)

    waves, *_ = run_cascade(G, ["JUNCTION"], enforce_edge_semantics=True)

    assert _failed(waves) == {"JUNCTION"}


def test_hospital_fails_when_it_loses_its_last_power_feed():
    G = nx.DiGraph()
    _asset(G, "GRID", "power_substation")
    _asset(G, "WATER", "water_station")
    _asset(G, "HOSP", "hospital")
    G.add_edge("GRID", "HOSP", edge_type="power_supply", capacity=100.0)
    G.add_edge("WATER", "HOSP", edge_type="water_supply", capacity=100.0)

    waves, *_ = run_cascade(G, ["GRID"], enforce_edge_semantics=True)

    assert "HOSP" in _failed(waves)


def test_hospital_survives_while_one_power_feed_remains():
    """Losing *a* supplier is not losing *every* supplier."""
    G = nx.DiGraph()
    _asset(G, "GRID_A", "power_substation")
    _asset(G, "GRID_B", "power_substation")
    _asset(G, "WATER", "water_station")
    _asset(G, "HOSP", "hospital")
    for feed in ("GRID_A", "GRID_B"):
        G.add_edge(feed, "HOSP", edge_type="power_supply", capacity=100.0)
    G.add_edge("WATER", "HOSP", edge_type="water_supply", capacity=100.0)

    waves, *_ = run_cascade(G, ["GRID_A"], enforce_edge_semantics=True)

    assert "HOSP" not in _failed(waves)


def test_hospital_also_fails_on_losing_water_alone():
    """Both services are required, so either one is sufficient to take it down."""
    G = nx.DiGraph()
    _asset(G, "GRID", "power_substation")
    _asset(G, "WATER", "water_station")
    _asset(G, "HOSP", "hospital")
    G.add_edge("GRID", "HOSP", edge_type="power_supply", capacity=100.0)
    G.add_edge("WATER", "HOSP", edge_type="water_supply", capacity=100.0)

    waves, *_ = run_cascade(G, ["WATER"], enforce_edge_semantics=True)

    assert "HOSP" in _failed(waves)


def test_an_unmodelled_dependency_is_not_a_severed_one():
    """A power-only dataset must not fail every hospital for a missing water main."""
    G = nx.DiGraph()
    _asset(G, "GRID_A", "power_substation")
    _asset(G, "GRID_B", "power_substation")
    _asset(G, "HOSP", "hospital")
    G.add_edge("GRID_A", "HOSP", edge_type="power_supply", capacity=100.0)
    G.add_edge("GRID_B", "HOSP", edge_type="power_supply", capacity=100.0)

    waves, *_ = run_cascade(G, ["GRID_A"], enforce_edge_semantics=True)

    assert _failed(waves) == {"GRID_A"}


def test_semantics_are_opt_in_and_off_by_default():
    """Default remains pure load-overload Motter-Lai for every existing caller."""
    G = nx.DiGraph()
    _asset(G, "GRID", "power_substation")
    _asset(G, "HOSP", "hospital")
    G.add_edge("GRID", "HOSP", edge_type="power_supply", capacity=100.0)

    without = _failed(run_cascade(G, ["GRID"])[0])
    with_semantics = _failed(run_cascade(G, ["GRID"], enforce_edge_semantics=True)[0])

    assert without == {"GRID"}
    assert with_semantics == {"GRID", "HOSP"}


def test_every_engine_edge_type_is_mirrored_to_neo4j():
    """An unmapped edge type is silently left out of the Neo4j mirror, so GDS
    centrality would rank a different graph from the NetworkX fallback."""
    from app.services.graph_sync import RELATIONSHIP_TYPES
    from app.simulation.semantics import EDGE_TYPES

    assert set(RELATIONSHIP_TYPES) == set(EDGE_TYPES)


def test_scenario_add_edge_accepts_every_engine_edge_type():
    """Recommendation payloads copy an existing link's type into add_edge."""
    import typing

    from app.api.scenarios import AddEdgeModification
    from app.simulation.semantics import EDGE_TYPES

    hint = typing.get_type_hints(AddEdgeModification)["edge_type"]
    accepted = set(typing.get_args(hint))
    assert accepted == set(EDGE_TYPES)
