"""
Unit tests for population impact calculation, capping, and overlap detection.
"""

from __future__ import annotations

import networkx as nx
import pytest

from app.simulation.population import STUDY_AREA_POPULATION_CAP, calculate_population_impact


@pytest.fixture
def sample_graph() -> nx.DiGraph:
    G = nx.DiGraph()
    G.add_node("N1", population_served=25_000)
    G.add_node("N2", population_served=40_000)
    G.add_node("N3", population_served=10_000)
    G.add_node("RJ1", population_served=0)
    G.add_node("RJ2", population_served=0)
    G.add_node("MEGA", population_served=100_000)
    return G


def test_empty_failures(sample_graph):
    result = calculate_population_impact([], sample_graph)
    assert result["raw_sum"] == 0
    assert result["population_affected_estimate"] == 0
    assert result["is_population_capped"] is False
    assert result["has_unresolved_overlap"] is False


def test_single_node_under_cap(sample_graph):
    result = calculate_population_impact(["N1"], sample_graph)
    assert result["raw_sum"] == 25_000
    assert result["population_affected_estimate"] == 25_000
    assert result["is_population_capped"] is False
    assert result["has_unresolved_overlap"] is False


def test_single_node_over_cap(sample_graph):
    result = calculate_population_impact(["MEGA"], sample_graph)
    assert result["raw_sum"] == 100_000
    assert result["population_affected_estimate"] == STUDY_AREA_POPULATION_CAP
    assert result["is_population_capped"] is True
    assert result["has_unresolved_overlap"] is False


def test_exact_boundary_at_65000(sample_graph):
    # N1 (25,000) + N2 (40,000) = 65,000
    result = calculate_population_impact(["N1", "N2"], sample_graph)
    assert result["raw_sum"] == 65_000
    assert result["population_affected_estimate"] == 65_000
    assert result["is_population_capped"] is False
    assert result["has_unresolved_overlap"] is True


def test_just_below_boundary_at_64999():
    G = nx.DiGraph()
    G.add_node("A", population_served=30_000)
    G.add_node("B", population_served=34_999)
    result = calculate_population_impact(["A", "B"], G)
    assert result["raw_sum"] == 64_999
    assert result["population_affected_estimate"] == 64_999
    assert result["is_population_capped"] is False
    assert result["has_unresolved_overlap"] is True


def test_just_above_boundary_at_65001():
    G = nx.DiGraph()
    G.add_node("A", population_served=30_000)
    G.add_node("B", population_served=35_001)
    result = calculate_population_impact(["A", "B"], G)
    assert result["raw_sum"] == 65_001
    assert result["population_affected_estimate"] == 65_000
    assert result["is_population_capped"] is True
    assert result["has_unresolved_overlap"] is True


def test_two_nodes_with_zero_population_no_unresolved_overlap(sample_graph):
    result = calculate_population_impact(["RJ1", "RJ2"], sample_graph)
    assert result["raw_sum"] == 0
    assert result["population_affected_estimate"] == 0
    assert result["is_population_capped"] is False
    assert result["has_unresolved_overlap"] is False


def test_missing_nodes_handled_gracefully(sample_graph):
    result = calculate_population_impact(["NON_EXISTENT", "N1"], sample_graph)
    assert result["raw_sum"] == 25_000
    assert result["population_affected_estimate"] == 25_000
    assert result["is_population_capped"] is False
    assert result["has_unresolved_overlap"] is False


# ---------------------------------------------------------------------------
# Capped before/after comparison
# ---------------------------------------------------------------------------

def _comparable(impact: dict) -> int:
    """Mirror of the figure the UI, demo script, and e2e test compare on."""
    raw = impact.get("raw_population_affected")
    return impact["population_affected_estimate"] if raw is None else raw


def test_capped_estimate_hides_improvement_but_raw_total_reveals_it():
    """
    Both a baseline and an intervention can exceed the study-area cap. The
    capped headline figure then reports the same number for each, so a strict
    "did population improve?" check on it reads as no change even when the
    intervention protected tens of thousands of people.

    This is what previously made the e2e loop's population assertion fail and
    made the UI contradict the recommendation panel. Comparisons must therefore
    use the uncapped total.
    """
    G = nx.DiGraph()
    G.add_node("A", population_served=80_000)
    G.add_node("B", population_served=44_439)
    G.add_node("C", population_served=5_740)

    baseline = calculate_population_impact({"A", "B", "C"}, G)      # 130,179 raw
    intervention = calculate_population_impact({"A", "C"}, G)       # 85,740 raw

    # Both saturate the cap, so the headline figures are indistinguishable.
    assert baseline["is_population_capped"] is True
    assert intervention["is_population_capped"] is True
    assert (
        baseline["population_affected_estimate"]
        == intervention["population_affected_estimate"]
        == STUDY_AREA_POPULATION_CAP
    )

    # The uncapped totals preserve the real improvement.
    assert intervention["raw_population_affected"] < baseline["raw_population_affected"]
    assert _comparable(baseline) - _comparable(intervention) == 44_439


def test_comparable_population_falls_back_to_capped_estimate_when_raw_absent():
    """Records predating raw_population_affected must still compare cleanly."""
    legacy = {"population_affected_estimate": 12_000}
    assert _comparable(legacy) == 12_000


def test_uncapped_total_is_always_reported_even_when_capped():
    """The raw figure must survive capping so callers can still compare."""
    G = nx.DiGraph()
    G.add_node("HUGE", population_served=500_000)

    impact = calculate_population_impact({"HUGE"}, G)

    assert impact["population_affected_estimate"] == STUDY_AREA_POPULATION_CAP
    assert impact["raw_population_affected"] == 500_000
    assert impact["is_population_capped"] is True
