"""The project's coordinate conventions, in one place.

Two orderings are in play and they are trivially confusable:

* **GeoJSON positions are ``[longitude, latitude]``** -- RFC 7946 section 3.1.1,
  and what PostGIS ``POINT(x y)``, MapLibre and deck.gl all expect.
* **Scalar fields are named ``lat`` and ``lng``** and carry no ordering at all,
  which is why the wire format for a node keeps them as two named scalars
  rather than an array.

Anywhere a coordinate becomes a positional pair, it goes through here, so the
ordering is stated once instead of being re-derived (and eventually reversed)
at each call site.
"""

from __future__ import annotations

#: Rough bounds of the Manipal study area, used to sanity-check ingested data.
#: A latitude/longitude swap moves a point thousands of kilometres, so a simple
#: bounding-box assertion catches it immediately.
STUDY_AREA_BBOX = (74.6, 13.2, 74.95, 13.5)  # (min_lng, min_lat, max_lng, max_lat)


def to_geojson_position(lat: float, lng: float) -> list[float]:
    """Return a GeoJSON position: ``[longitude, latitude]``."""
    return [float(lng), float(lat)]


def from_geojson_position(position: list[float] | tuple[float, ...]) -> tuple[float, float]:
    """Unpack a GeoJSON position into ``(lat, lng)``."""
    lng, lat = float(position[0]), float(position[1])
    return lat, lng


def to_ewkt_point(lat: float, lng: float, srid: int = 4326) -> str:
    """Return an EWKT POINT. PostGIS takes ``POINT(x y)`` -- longitude first."""
    return f"SRID={srid};POINT({float(lng)} {float(lat)})"


def within_study_area(lat: float, lng: float) -> bool:
    """True when the point lies inside the study-area bounding box."""
    min_lng, min_lat, max_lng, max_lat = STUDY_AREA_BBOX
    return min_lng <= lng <= max_lng and min_lat <= lat <= max_lat
