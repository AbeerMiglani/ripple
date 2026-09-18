"""
Population impact calculation service for the Ripple simulator.

Provides spatially deduplicated population estimation, zone capping, and
honest unresolved-overlap reporting.

The engine previously summed ``population_served`` across every failed asset.
Service areas overlap -- a neighbourhood fed by one substation is very often
also fed by one water station -- so that sum counted the same residents once
per asset serving them, routinely exceeding the entire study area's census.
A blanket ``min(total, 65_000)`` clamp hid the symptom without fixing it, and
destroyed the signal whenever two runs both saturated the cap.

Here, when assets carry service geometry, their service areas are unioned and
each resident is counted exactly once.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import networkx as nx

logger = logging.getLogger(__name__)

STUDY_AREA_POPULATION_CAP = 65_000

#: Fallback service radius in metres, by asset class, used when a node carries
#: no explicit ``service_radius_m``. These are coarse planning figures, not
#: measurements -- which is exactly why a run that relies on them still reports
#: its population figure as estimated rather than observed.
SERVICE_RADIUS_DEFAULTS_M: dict[str, float] = {
    "power_substation": 1200.0,
    "water_station": 900.0,
    "hospital": 2500.0,
    "telecom_tower": 1500.0,
    "road_junction": 300.0,
}

#: Metres per degree of latitude (WGS84 mean). Longitude is scaled by the
#: cosine of the working latitude. Over a single municipality this local
#: equirectangular projection is accurate to well under a percent, which is far
#: inside the uncertainty of the population figures themselves -- and it avoids
#: taking a hard dependency on pyproj for a study area a few kilometres across.
_M_PER_DEG_LAT = 111_320.0


def effective_service_radius_m(node_data: dict[str, Any]) -> float | None:
    """The radius to use for this asset, or None when it cannot be determined."""
    declared = node_data.get("service_radius_m")
    if declared is not None:
        try:
            radius = float(declared)
        except (TypeError, ValueError):
            return None
        return radius if radius > 0 else None
    return SERVICE_RADIUS_DEFAULTS_M.get(str(node_data.get("node_type", "")))


def _service_area(node_data: dict[str, Any], origin: tuple[float, float], radius_m: float):
    """Project one asset's service area into local metres and buffer it.

    Returns None when shapely is unavailable or the node has no usable position,
    which pushes the caller onto the additive fallback rather than failing.
    """
    try:
        from shapely.geometry import Point
    except ImportError:  # pragma: no cover - shapely is a declared dependency
        logger.warning("shapely unavailable; population overlap cannot be resolved")
        return None

    lat, lng = node_data.get("lat"), node_data.get("lng")
    if lat is None or lng is None:
        return None
    try:
        lat_f, lng_f = float(lat), float(lng)
    except (TypeError, ValueError):
        return None

    lat0, lng0 = origin
    m_per_deg_lng = _M_PER_DEG_LAT * math.cos(math.radians(lat0))
    x = (lng_f - lng0) * m_per_deg_lng
    y = (lat_f - lat0) * _M_PER_DEG_LAT
    return Point(x, y).buffer(radius_m, quad_segs=16)


def _deduplicate(located: list[tuple[str, int, Any]]) -> int:
    """Count each resident once across overlapping service areas.

    Every asset is treated as serving its own area at a uniform density. Where
    areas overlap the residents are the *same* residents, so the overlap is
    attributed to the densest provider covering it and counted a single time:
    two identical areas of 1,000 people each resolve to 1,000, not 2,000.

    Implemented as a single descending-density pass -- ``own = P_i - claimed``,
    then ``claimed |= P_i`` -- rather than a pairwise intersection sweep or a
    full planar decomposition of the union. That is O(n log n) shapely
    operations instead of O(n^2), which matters because the recommendation
    engine calls this once per candidate rerun inside a request handler.
    """
    from shapely.ops import unary_union

    scored = []
    for node_id, population, area in located:
        footprint = area.area
        if footprint <= 0:
            continue
        scored.append((population / footprint, node_id, population, area))

    # Densest first; node id breaks ties so the result is reproducible.
    scored.sort(key=lambda item: (-item[0], item[1]))

    total = 0.0
    claimed: Any = None
    for density, _node_id, _population, area in scored:
        exclusive = area if claimed is None else area.difference(claimed)
        if not exclusive.is_empty:
            total += density * exclusive.area
        claimed = area if claimed is None else unary_union([claimed, area])

    return int(round(total))


def calculate_population_impact(
    failed_node_ids: set[str] | list[str],
    G_baseline: nx.DiGraph | None = None,
    study_area_cap: int = STUDY_AREA_POPULATION_CAP,
    **kwargs: Any,
) -> dict[str, Any]:
    """
    Computes population impact with spatial deduplication, a municipal cap, and
    unresolved overlap detection.

    Guarantees:
    1. population_affected_estimate never exceeds study_area_cap.
    2. is_population_capped is True iff the pre-cap figure exceeds study_area_cap.
    3. has_unresolved_overlap is True only when two or more populated assets
       failed and their overlap could NOT be resolved spatially.
    4. raw_sum / raw_population_affected remain the uncapped additive sum, so
       comparisons against runs recorded before deduplication existed still work.
    """
    graph = G_baseline if G_baseline is not None else kwargs.get("G")
    if graph is None:
        raise ValueError("A baseline NetworkX DiGraph must be provided.")

    failed_set = {nid for nid in failed_node_ids if nid in graph.nodes}
    raw_sum = sum(
        int(graph.nodes[nid].get("population_served", 0)) for nid in failed_set
    )

    populated = sorted(
        nid for nid in failed_set
        if int(graph.nodes[nid].get("population_served", 0)) > 0
    )

    # Split the populated casualties into those we can place on the map and
    # those we cannot. Only the former can have their overlap resolved.
    locatable: list[str] = []
    unlocatable: list[str] = []
    for nid in populated:
        data = graph.nodes[nid]
        radius = effective_service_radius_m(data)
        if radius and data.get("lat") is not None and data.get("lng") is not None:
            locatable.append(nid)
        else:
            unlocatable.append(nid)

    deduplicated: int | None = None
    if locatable:
        lat0 = sum(float(graph.nodes[n]["lat"]) for n in locatable) / len(locatable)
        lng0 = sum(float(graph.nodes[n]["lng"]) for n in locatable) / len(locatable)
        located: list[tuple[str, int, Any]] = []
        for nid in locatable:
            data = graph.nodes[nid]
            radius = effective_service_radius_m(data)
            area = _service_area(data, (lat0, lng0), float(radius or 0.0))
            if area is not None and not area.is_empty:
                located.append((nid, int(data.get("population_served", 0)), area))
            else:
                unlocatable.append(nid)
        if located:
            deduplicated = _deduplicate(located)
            # Assets we could not place are still real casualties; their
            # populations are added back additively, which is why the result is
            # reported as only partially resolved below.
            deduplicated += sum(
                int(graph.nodes[nid].get("population_served", 0)) for nid in unlocatable
            )

    if deduplicated is None:
        dedup_method = "additive"
        pre_cap = raw_sum
    elif unlocatable:
        dedup_method = "partial"
        pre_cap = deduplicated
    else:
        dedup_method = "spatial"
        pre_cap = deduplicated

    is_capped = pre_cap > study_area_cap
    capped_estimate = min(pre_cap, study_area_cap)

    # Honest reporting: overlap is "unresolved" only when two or more populated
    # assets failed and at least one pair of them could not be compared
    # geometrically. Previously this was True whenever two populated nodes
    # failed, even if their service areas were kilometres apart.
    unresolved_count = len(unlocatable) + (1 if len(unlocatable) < len(populated) else 0)
    has_unresolved_overlap = len(populated) >= 2 and unresolved_count >= 2

    return {
        "raw_sum": raw_sum,
        "raw_population_affected": raw_sum,
        "deduplicated_population_affected": deduplicated,
        "overlap_population": None if deduplicated is None else max(0, raw_sum - deduplicated),
        "dedup_method": dedup_method,
        "population_affected_estimate": capped_estimate,
        "study_area_population_cap": study_area_cap,
        "is_population_capped": is_capped,
        "has_unresolved_overlap": has_unresolved_overlap,
    }
