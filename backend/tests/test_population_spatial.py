"""Spatial deduplication of overlapping service areas.

Summing `population_served` across failed assets counts anyone served by two of
them twice. On the demo network that routinely exceeded the whole study area's
census, and the blanket cap that hid it also destroyed the signal whenever two
runs both saturated it.
"""

from __future__ import annotations

import networkx as nx

from app.simulation.population import (
    SERVICE_RADIUS_DEFAULTS_M,
    STUDY_AREA_POPULATION_CAP,
    calculate_population_impact,
    effective_service_radius_m,
)

LAT, LNG = 13.35, 74.79


def _served(G, node_id, population, *, lat=LAT, lng=LNG, radius=1_000.0,
            node_type="power_substation"):
    G.add_node(
        node_id,
        node_type=node_type,
        population_served=population,
        lat=lat,
        lng=lng,
        service_radius_m=radius,
    )


def test_two_assets_serving_the_same_area_count_its_people_once():
    """The headline double-count: 1,000 + 1,000 over one area is 1,000 people."""
    G = nx.DiGraph()
    _served(G, "SUB", 1_000)
    _served(G, "WTR", 1_000, node_type="water_station")

    result = calculate_population_impact({"SUB", "WTR"}, G)

    assert result["raw_sum"] == 2_000
    assert result["deduplicated_population_affected"] == 1_000
    assert result["overlap_population"] == 1_000
    assert result["dedup_method"] == "spatial"
    assert result["population_affected_estimate"] == 1_000


def test_genuinely_disjoint_areas_still_sum():
    """Deduplication must not quietly merge unrelated neighbourhoods."""
    G = nx.DiGraph()
    _served(G, "A", 1_000)
    _served(G, "B", 1_000, lat=LAT + 0.18)  # ~20 km north

    result = calculate_population_impact({"A", "B"}, G)

    assert result["deduplicated_population_affected"] == 2_000
    assert result["overlap_population"] == 0


def test_partial_overlap_lands_between_the_two_extremes():
    G = nx.DiGraph()
    _served(G, "A", 1_000)
    _served(G, "B", 1_000, lat=LAT + 0.009)  # ~1 km north, radius 1 km each

    result = calculate_population_impact({"A", "B"}, G)
    deduplicated = result["deduplicated_population_affected"]

    assert 1_000 < deduplicated < 2_000
    assert result["overlap_population"] == 2_000 - deduplicated


def test_overlap_is_reported_resolved_once_it_actually_is():
    """`has_unresolved_overlap` was True for any two populated casualties."""
    G = nx.DiGraph()
    _served(G, "A", 1_000)
    _served(G, "B", 1_000)

    assert calculate_population_impact({"A", "B"}, G)["has_unresolved_overlap"] is False


def test_overlap_stays_unresolved_without_geometry():
    """Honest fallback: no geometry means the overlap genuinely is unknown."""
    G = nx.DiGraph()
    G.add_node("A", population_served=1_000)
    G.add_node("B", population_served=1_000)

    result = calculate_population_impact({"A", "B"}, G)

    assert result["dedup_method"] == "additive"
    assert result["deduplicated_population_affected"] is None
    assert result["has_unresolved_overlap"] is True
    assert result["population_affected_estimate"] == 2_000


def test_a_partially_located_set_is_reported_as_partial():
    G = nx.DiGraph()
    _served(G, "A", 1_000)
    G.add_node("B", population_served=1_000)  # no geometry

    result = calculate_population_impact({"A", "B"}, G)

    assert result["dedup_method"] == "partial"
    assert result["has_unresolved_overlap"] is True


def test_node_type_supplies_a_radius_when_none_is_declared():
    assert effective_service_radius_m({"node_type": "hospital"}) == (
        SERVICE_RADIUS_DEFAULTS_M["hospital"]
    )
    assert effective_service_radius_m({"service_radius_m": 500.0}) == 500.0
    assert effective_service_radius_m({"node_type": "unknown_type"}) is None
    assert effective_service_radius_m({"service_radius_m": 0}) is None


def test_deduplicated_total_is_still_capped_to_the_study_area():
    G = nx.DiGraph()
    _served(G, "A", 500_000, radius=1_000.0)

    result = calculate_population_impact({"A"}, G)

    assert result["population_affected_estimate"] == STUDY_AREA_POPULATION_CAP
    assert result["is_population_capped"] is True
    assert result["raw_population_affected"] == 500_000


def test_deduplication_keeps_an_improvement_visible_that_capping_would_hide():
    """The reason the raw total was persisted in the first place."""
    G = nx.DiGraph()
    _served(G, "A", 30_000)
    _served(G, "B", 30_000, lat=LAT + 0.18)
    _served(G, "C", 30_000, lat=LAT + 0.36)

    baseline = calculate_population_impact({"A", "B", "C"}, G)
    intervention = calculate_population_impact({"A", "B"}, G)

    assert baseline["deduplicated_population_affected"] == 90_000
    assert intervention["deduplicated_population_affected"] == 60_000
