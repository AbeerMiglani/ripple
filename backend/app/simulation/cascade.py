from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import networkx as nx

from app.simulation.semantics import edge_services, required_services

logger = logging.getLogger(__name__)

#: Default wave guardrail. The engine stays free of application configuration
#: so it can be driven standalone (tests, diagnosis, stress runs) without a
#: database or settings object; callers that have settings inject their own
#: bound via ``max_waves`` -- see ``app.simulation.runner``.
DEFAULT_MAX_WAVES = 50


def calculate_global_efficiency(G: nx.DiGraph, N_baseline: int | None = None) -> float:
    """
    Calculates the global efficiency of the graph.
    Formula: E = (1 / (N*(N-1))) * sum(1 / d(i,j)) for all i != j
    """
    N = N_baseline if N_baseline is not None else len(G)

    if N < 2:
        return 0.0

    denom = N * (N - 1)
    g_eff = 0.0

    # We always do manual calculation to ensure we normalize by N_baseline
    # because nx.global_efficiency normalizes by len(G), which inflates scores
    # for small surviving subgraphs.
    for source, lengths in nx.all_pairs_shortest_path_length(G):
        for target, distance in lengths.items():
            if source != target and distance > 0:
                g_eff += 1.0 / distance

    return g_eff / denom


def _build_supplier_index(G: nx.DiGraph) -> dict[str, dict[str, set[str]]]:
    """Index, per node, which upstream assets deliver each critical service.

    Built once against the *baseline* topology so that "this asset has lost
    every one of its power feeds" is answerable in O(1) per check during the
    cascade instead of re-walking predecessors each wave.

    A service the node declares but for which the dataset models no supplier at
    all is dropped: an unmodelled dependency must not be read as a severed one,
    or every hospital in a power-only dataset would fail instantly for want of
    a water main nobody drew.
    """
    index: dict[str, dict[str, set[str]]] = {}
    for v in G.nodes:
        needed = required_services(G.nodes[v])
        if not needed:
            continue
        per_service: dict[str, set[str]] = {service: set() for service in needed}
        for u in G.predecessors(v):
            for service in edge_services(G.get_edge_data(u, v) or {}):
                if service in per_service:
                    per_service[service].add(str(u))
        modelled = {service: sup for service, sup in per_service.items() if sup}
        if modelled:
            index[str(v)] = modelled
    return index


def _dependencies_severed(
    node: str,
    supplier_index: dict[str, dict[str, set[str]]],
    failed: set[str],
) -> bool:
    """True when the node has lost every supplier of at least one critical service."""
    return any(
        suppliers.issubset(failed)
        for suppliers in supplier_index.get(node, {}).values()
    )


def run_cascade(
    G_baseline: nx.DiGraph,
    initial_failures: list[str],
    max_waves: int = DEFAULT_MAX_WAVES,
    on_wave_completed: Callable[[dict[str, Any]], None] | None = None,
    enforce_edge_semantics: bool = False,
) -> tuple[list[dict[str, Any]], float, float, int, bool]:
    """
    Runs the Motter-Lai load redistribution cascade algorithm in-memory.

    Returns ``(waves, efficiency_before, efficiency_after, population_affected,
    stabilized)``.

    Every wave is evaluated as a strict two-phase commit. Phase one reads only
    the graph state as it stood at the end of wave N-1 and decides, for every
    surviving node, whether it fails; phase two applies those failures and the
    load they shed simultaneously. No node can therefore observe a half-applied
    wave, and the outcome does not depend on the order nodes happen to be
    visited in -- which is what previously made interdependent assets (power
    feeding water feeding power) resolve differently run to run.

    Failure is latched: a node that fails is marked ``has_failed`` and is never
    re-evaluated, so a dependency cycle settles instead of oscillating between
    operational and failed forever. Failed nodes stay *in* the graph rather than
    being deleted, so downstream analysis can still ask what a severed node was
    connected to.

    ``enforce_edge_semantics`` turns on domain-aware propagation: a node fails
    when it loses every supplier of a service it cannot operate without (see
    ``app.simulation.semantics``), so a blocked road no longer de-energises an
    electrical tower while a hospital still requires both power and water. It
    defaults to False, which is pure load-overload Motter-Lai -- the behaviour
    every existing caller and characterization test expects.

    ``stabilized`` is False when the cascade was still producing new failures at
    ``max_waves`` and was therefore truncated. A truncated cascade is a valid
    bounded result and is returned normally: the guardrail bounds the work, it
    does not invalidate the analysis. Callers are expected to surface the flag
    so a truncated run is never presented as a settled one.
    """
    unknown_initial_failures = set(initial_failures) - set(G_baseline.nodes)
    if unknown_initial_failures:
        raise ValueError("initial_failures contains nodes outside the simulation graph")

    # NEVER mutate the Neo4j baseline or the provided baseline graph.
    G = G_baseline.copy()

    eff_before = calculate_global_efficiency(G)

    supplier_index = _build_supplier_index(G) if enforce_edge_semantics else {}

    waves: list[dict[str, Any]] = []
    #: The monotonic latch. Membership is permanent -- nothing ever removes a
    #: node from this set, which is what makes a cyclic dependency terminate.
    failed: set[str] = set()
    failed_in_wave = set(initial_failures)

    current_wave_idx = 0

    while failed_in_wave and current_wave_idx < max_waves:
        # --- Phase 1: commit this wave's failures, all at once -------------
        # Latching before any load moves means a node that fails later in the
        # same wave still refuses load, regardless of iteration order.
        for u in failed_in_wave:
            failed.add(u)
            if u in G:
                G.nodes[u]["has_failed"] = True

        marginal = sorted(failed_in_wave)
        wave_data = {
            "wave": current_wave_idx,
            # Marginal set: the nodes that newly failed in THIS wave. Kept under
            # the original key as well as the explicit one so persisted results,
            # the API schema and existing consumers keep working unchanged.
            "failed_node_ids": marginal,
            "marginal_failed_node_ids": marginal,
            # Cumulative set: everything failed up to and including this wave.
            # Published explicitly so consumers stop having to re-accumulate it
            # themselves and stop mistaking one for the other.
            "cumulative_failed_node_ids": sorted(failed),
        }
        waves.append(wave_data)

        # Publish real-time event if callback provided
        if on_wave_completed:
            on_wave_completed(wave_data)

        # --- Phase 2: evaluate redistribution against frozen state ---------
        # Deltas accumulate into `pending` and are applied together below, so
        # every transfer this wave is computed from the same snapshot.
        pending: dict[str, float] = {}
        for u in sorted(failed_in_wave):
            if u not in G:
                continue

            load_to_distribute = G.nodes[u].get('current_load', 0.0)

            # Transfer only through surviving outgoing links. Link capacity is
            # a hard upper bound; when no capacity is modelled (legacy tests),
            # retain the previous uniform redistribution behaviour.
            surviving_links = sorted(
                [
                    (v, G.get_edge_data(u, v) or {})
                    for v in G.successors(u)
                    if v not in failed
                ],
                key=lambda x: x[0]
            )

            if surviving_links and load_to_distribute > 0:
                declared_capacities = [edge.get("capacity") for _, edge in surviving_links]
                if any(capacity is not None for capacity in declared_capacities):
                    capacities = [max(0.0, float(edge.get("capacity", 0.0))) for _, edge in surviving_links]
                    total_capacity = sum(capacities)
                    if total_capacity > 0:
                        for (neighbor, _), link_capacity in zip(surviving_links, capacities):
                            transferred_load = min(
                                link_capacity,
                                load_to_distribute * (link_capacity / total_capacity),
                            )
                            pending[neighbor] = pending.get(neighbor, 0.0) + transferred_load
                else:
                    delta = load_to_distribute / len(surviving_links)
                    for neighbor, _ in surviving_links:
                        pending[neighbor] = pending.get(neighbor, 0.0) + delta

        # --- Phase 3: apply every delta simultaneously ---------------------
        for neighbor, delta in pending.items():
            G.nodes[neighbor]["current_load"] = G.nodes[neighbor].get("current_load", 0.0) + delta

        # --- Phase 4: determine new failures for the NEXT wave -------------
        # Only nodes whose situation actually changed can newly fail: one that
        # received no load and lost no supplier is in exactly the state it was
        # judged survivable in a moment ago. The first pass is a full sweep so
        # a node already over its threshold in the baseline is still caught;
        # afterwards the frontier is enough, which turns an O(N)-per-wave sweep
        # into O(edges touched).
        if current_wave_idx == 0:
            candidates: set[str] = {str(n) for n in G.nodes}
        else:
            candidates = set(pending)
            if enforce_edge_semantics:
                for u in failed_in_wave:
                    if u in G:
                        candidates.update(str(v) for v in G.successors(u))

        failed_in_wave = set()
        for node in candidates:
            if node in failed or node not in G:
                continue
            data = G.nodes[node]
            threshold = data.get('capacity', 100.0) * data.get('failure_threshold', 1.0)
            if data.get('current_load', 0.0) > threshold:
                failed_in_wave.add(node)
            elif enforce_edge_semantics and _dependencies_severed(node, supplier_index, failed):
                failed_in_wave.add(node)

        current_wave_idx += 1

    # Nodes still failing at the guardrail mean the cascade had not settled.
    # Report the bounded result rather than discarding the whole simulation.
    stabilized = not failed_in_wave
    if not stabilized:
        logger.warning(
            "cascade truncated at max_waves=%s with %d node(s) still failing; "
            "returning bounded result",
            max_waves,
            len(failed_in_wave),
        )

    # Efficiency is measured over the survivors only, still normalized by the
    # baseline node count. Failed nodes remain in G (they are latched, not
    # deleted), so the surviving subgraph is taken explicitly.
    survivors = G.subgraph([n for n in G.nodes if n not in failed])
    eff_after = calculate_global_efficiency(survivors, N_baseline=len(G_baseline))

    # Naive uncapped total: this double counts overlapping service areas and is
    # retained only for backwards compatibility. Callers that need an honest
    # figure use app.simulation.population.calculate_population_impact, which
    # deduplicates overlapping areas spatially.
    pop_affected = 0
    for f in failed:
        if f in G_baseline.nodes:
            pop_affected += G_baseline.nodes[f].get('population_served', 0)

    return waves, eff_before, eff_after, pop_affected, stabilized
