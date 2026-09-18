"""Coordinate ordering, pinned rather than assumed.

GeoJSON positions are ``[longitude, latitude]`` (RFC 7946 3.1.1), which is also
what PostGIS ``POINT(x y)``, MapLibre and deck.gl expect. Nothing previously
checked the ordering end to end: the ingestion test mirrored the reader's own
``lat=coords[1], lng=coords[0]`` rather than verifying it, so a swapped pair in
the seed file would have passed. A swap moves the study area from coastal
Karnataka into the Arabian Sea off Somalia, which a bounding box catches at once.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.geo import (
    STUDY_AREA_BBOX,
    from_geojson_position,
    to_ewkt_point,
    to_geojson_position,
    within_study_area,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SEED_NODES_PATH = PROJECT_ROOT / "data" / "seed" / "nodes.geojson"


@pytest.fixture(scope="module")
def seed_features():
    with open(SEED_NODES_PATH, encoding="utf-8") as f:
        return json.load(f)["features"]


# ---------------------------------------------------------------------------
# The helpers themselves
# ---------------------------------------------------------------------------

def test_geojson_position_is_longitude_first():
    assert to_geojson_position(13.35, 74.79) == [74.79, 13.35]


def test_round_trip_preserves_orientation():
    assert from_geojson_position(to_geojson_position(13.35, 74.79)) == (13.35, 74.79)


def test_ewkt_point_is_x_then_y():
    """PostGIS POINT(x y): longitude is x."""
    assert to_ewkt_point(13.35, 74.79) == "SRID=4326;POINT(74.79 13.35)"


def test_bounding_box_rejects_a_swapped_pair():
    """Guard the guard -- otherwise every assertion below is vacuous."""
    assert within_study_area(13.35, 74.79) is True
    assert within_study_area(74.79, 13.35) is False


# ---------------------------------------------------------------------------
# The committed seed fixture
# ---------------------------------------------------------------------------

def test_every_seed_node_is_inside_the_study_area(seed_features):
    min_lng, min_lat, max_lng, max_lat = STUDY_AREA_BBOX
    for feature in seed_features:
        lng, lat = feature["geometry"]["coordinates"]
        assert min_lng <= lng <= max_lng, f"{feature['properties']['name']}: lng {lng}"
        assert min_lat <= lat <= max_lat, f"{feature['properties']['name']}: lat {lat}"


def test_seed_coordinates_are_not_transposed(seed_features):
    """Latitude and longitude ranges here do not overlap, so a swap is visible."""
    for feature in seed_features:
        lat, lng = from_geojson_position(feature["geometry"]["coordinates"])
        assert within_study_area(lat, lng)


# ---------------------------------------------------------------------------
# The OSM converter
# ---------------------------------------------------------------------------

class _FakeOSMGraph:
    """Minimal stand-in: OSMnx stores latitude as y and longitude as x."""

    def __init__(self, points):
        self._points = points

    def nodes(self, data=False):
        return [(osmid, {"y": lat, "x": lng}) for osmid, lat, lng in self._points]

    def edges(self, data=False):
        ids = [osmid for osmid, _, _ in self._points]
        return [(ids[i], ids[i + 1], {"length": 100.0}) for i in range(len(ids) - 1)]

    def has_edge(self, u, v):
        return False


def test_osm_converter_maps_y_to_latitude_and_x_to_longitude():
    from app.services.osm_ingestion import _convert_graph

    nodes, _edges = _convert_graph(
        _FakeOSMGraph([(1, 13.35, 74.79), (2, 13.36, 74.80)]), "Manipal"
    )

    assert [(n["lat"], n["lng"]) for n in nodes] == [(13.35, 74.79), (13.36, 74.80)]
    for node in nodes:
        assert within_study_area(node["lat"], node["lng"])


def test_osm_nodes_carry_the_provenance_the_ingestion_contract_expects():
    """These fields were missing, so OSM output could not be ingested cleanly."""
    from app.services.osm_ingestion import _convert_graph

    nodes, edges = _convert_graph(_FakeOSMGraph([(1, 13.35, 74.79), (2, 13.36, 74.80)]), "M")

    for node in nodes:
        assert node["is_synthetic"] is False
        assert node["data_source"] == "osm"
        assert node["display_name"]
        # "verified" is not in the vocabulary and "observed" would overclaim:
        # the geometry is observed but the capacities and loads are not.
        assert node["data_quality"] in {"observed", "estimated", "derived", "simulated"}
        assert node["data_quality"] not in {"observed", "verified"}

    for edge in edges:
        assert edge["is_synthetic"] is False
        assert edge["data_source"] == "osm"


def test_osm_ids_are_version_4_uuids():
    """pydantic UUID4 rejects a v5 id and substitutes an unrelated surrogate,
    so the API would report node IDs matching no node in the network."""
    import uuid

    from app.services.osm_ingestion import _convert_graph, _stable_id

    nodes, edges = _convert_graph(_FakeOSMGraph([(1, 13.35, 74.79), (2, 13.36, 74.80)]), "M")

    for record in [*nodes, *edges]:
        assert uuid.UUID(record["id"]).version == 4

    assert _stable_id("node", "M:1") == _stable_id("node", "M:1"), "ids must stay deterministic"
