"""Domain-specific edge semantics for the cascade engine.

The graph carries a ``edge_type`` on every link (``power_supply``,
``water_supply``, ``road_link``, ``depends_on``, and the explicit
``requires_*`` forms), but a pure Motter-Lai cascade treats every link as an
interchangeable load conduit. That is wrong in both directions: a blocked road
must not de-energise an electrical tower, and a hospital that keeps its power
but loses its water main is not "fine" merely because no load moved.

This module holds the vocabulary the engine reasons about. It deliberately
imports nothing from ``app.models`` or ``app.db``: ``app.simulation`` stays
runnable standalone (tests, diagnosis, stress runs) without a database or a
settings object, and the ORM enum in ``app.models.network`` is the persistence
mirror of these same strings rather than their source.
"""

from __future__ import annotations

from typing import Any

#: Every edge type the engine understands. ``requires_*`` are explicit
#: dependency declarations; the older supply/link names carry an implicit
#: dependency of the matching kind (see ``DEPENDENCY_EDGE_TYPES``).
EDGE_TYPES: frozenset[str] = frozenset(
    {
        "power_supply",
        "water_supply",
        "road_link",
        "depends_on",
        "requires_power",
        "requires_water",
        "requires_transit",
    }
)

#: Maps an edge type onto the *kind of service* it delivers. Two spellings can
#: deliver the same service: an asset fed by ``power_supply`` and one fed by
#: ``requires_power`` both depend on power, so a profile written in terms of
#: services matches either spelling without the caller having to know which
#: vocabulary a given dataset used.
SERVICE_BY_EDGE_TYPE: dict[str, str] = {
    "power_supply": "power",
    "requires_power": "power",
    "water_supply": "water",
    "requires_water": "water",
    "road_link": "transit",
    "requires_transit": "transit",
    # "depends_on" is the generic catch-all and is deliberately absent: it
    # names no particular service, so it can never be a *critical* dependency
    # of a specific kind. It still redistributes load like any other edge.
}

#: Which services each asset class cannot operate without.
#:
#: This is the rule that stops a blocked road from failing a substation. A
#: power_substation depends on power only, so severing its transit links leaves
#: it running; a hospital depends on power *and* water, so losing either one
#: takes it offline. A road_junction depends on transit alone.
#:
#: An asset type absent from this map has no critical dependency and can only
#: fail from load overload -- which is exactly the pre-existing behaviour.
CRITICAL_DEPENDENCIES: dict[str, frozenset[str]] = {
    "hospital": frozenset({"power", "water"}),
    "power_substation": frozenset({"power"}),
    "water_station": frozenset({"power"}),
    "telecom_tower": frozenset({"power"}),
    "road_junction": frozenset({"transit"}),
}


def edge_services(edge_data: dict[str, Any]) -> set[str]:
    """Return the service kinds one edge delivers.

    ``edge_types`` (plural) is read first: a ``DiGraph`` can only hold one edge
    per ordered pair, so the graph builders merge parallel typed links between
    the same two assets into a set rather than letting the last one win. Falls
    back to the singular ``edge_type`` for graphs built elsewhere.
    """
    declared = edge_data.get("edge_types")
    if not declared:
        single = edge_data.get("edge_type")
        declared = [single] if single else []
    return {
        SERVICE_BY_EDGE_TYPE[str(t)]
        for t in declared
        if str(t) in SERVICE_BY_EDGE_TYPE
    }


def required_services(node_data: dict[str, Any]) -> frozenset[str]:
    """Return the services this asset cannot operate without."""
    return CRITICAL_DEPENDENCIES.get(str(node_data.get("node_type", "")), frozenset())
