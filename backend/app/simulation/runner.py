from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc

import networkx as nx
from celery import shared_task
from sqlalchemy.orm import Session

from app.config import settings
from app.db.postgres import SessionLocal
from app.db.redis import get_redis_client
from app.models.network import Edge, Node, Scenario, SimulationResult
from app.services.graph_build import build_graph
from app.simulation.cascade import run_cascade
from app.simulation.isolation import isolated_graph
from app.simulation.population import calculate_population_impact

logger = logging.getLogger(__name__)


def _setting(name: str, default):
    """Read a setting defensively.

    The test suite replaces ``app.config`` with a MagicMock, so a bare
    ``settings.x`` returns a truthy Mock rather than a value. Anything read on
    a path the tests exercise goes through here, which coerces to the expected
    type and falls back to the documented default.
    """
    value = getattr(settings, name, default)
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, int):
        return value if isinstance(value, int) else default
    return value if isinstance(value, type(default)) else default


def apply_scenario_modifications(
    G: nx.DiGraph,
    modifications: list[dict],
) -> nx.DiGraph:
    """
    Applies polymorphic scenario modifications (add_edge and upgrade_node)
    to a copy of the in-memory graph.
    """
    G_mod = G.copy()
    for mod in modifications:
        mod_type = mod.get("type")
        if mod_type == "add_edge":
            src = str(mod["source"])
            tgt = str(mod["target"])
            if src not in G_mod or tgt not in G_mod or src == tgt:
                raise ValueError("scenario references invalid graph endpoints")
            weight = float(mod.get("weight", 1.0))
            capacity = float(mod.get("capacity", 100.0))
            edge_type = mod.get("edge_type", "power_supply")
            is_bi = bool(mod.get("is_bidirectional", False))

            G_mod.add_edge(src, tgt, weight=weight, capacity=capacity, edge_type=edge_type)
            if is_bi:
                G_mod.add_edge(tgt, src, weight=weight, capacity=capacity, edge_type=edge_type)

        elif mod_type == "upgrade_node":
            nid = str(mod["node_id"])
            if nid not in G_mod:
                raise ValueError(f"scenario upgrade references unknown node {nid}")

            if mod.get("capacity") is not None:
                G_mod.nodes[nid]["capacity"] = float(mod["capacity"])
            elif mod.get("capacity_multiplier") is not None:
                G_mod.nodes[nid]["capacity"] *= float(mod["capacity_multiplier"])
            elif mod.get("capacity_add") is not None:
                G_mod.nodes[nid]["capacity"] += float(mod["capacity_add"])

            if mod.get("failure_threshold") is not None:
                G_mod.nodes[nid]["failure_threshold"] = float(mod["failure_threshold"])
            elif mod.get("failure_threshold_add") is not None:
                G_mod.nodes[nid]["failure_threshold"] += float(mod["failure_threshold_add"])

        else:
            raise ValueError(f"unsupported scenario modification type: {mod_type}")

    return G_mod


@shared_task(bind=True)
def run_simulation_task(
    self, 
    simulation_id: str, 
    network_id: str, 
    initial_failures: list[str],
    scenario_id: str | None = None
):
    """
    Background Celery task to run the cascade simulation.
    If scenario_id is provided, applies network modifications first.
    Publishes wave events to Redis for WebSocket streaming.
    """
    db: Session = SessionLocal()
    try:
        sim = db.query(SimulationResult).filter(SimulationResult.id == simulation_id).first()
        if not sim:
            return
        
        sim.status = "running"
        db.commit()
        
        # 1. Fetch network topology
        nodes = db.query(Node).filter(Node.network_id == network_id).all()
        edges = db.query(Edge).filter(Edge.network_id == network_id).all()
        
        # 2. Build in-memory NetworkX DiGraph. The shared builder carries
        #    node_type and geometry, which domain-aware propagation and
        #    population deduplication both need.
        G = build_graph(nodes, edges)

        # 3. Apply Scenario Modifications if present
        scenario = None
        if scenario_id:
            scenario = db.query(Scenario).filter(Scenario.id == scenario_id).first()
            if not scenario or str(scenario.network_id) != network_id:
                raise ValueError("scenario does not belong to the simulation network")
            if scenario.modifications:
                G = apply_scenario_modifications(G, scenario.modifications)
                            
        # Callback to publish waves to Redis
        def on_wave(wave_data):
            # Publish to Redis channel specific to this simulation
            get_redis_client().publish(f"sim_{simulation_id}", json.dumps(wave_data))

        # 4. Run cascade engine against an explicitly isolated copy, so a
        #    later scenario in this worker starts from a clean baseline rather
        #    than from the degraded state this run leaves behind.
        with isolated_graph(G) as G_run:
            waves, eff_before, eff_after, pop_affected, stabilized = run_cascade(
                G_run,
                initial_failures,
                max_waves=_setting("max_cascade_waves", 50),
                on_wave_completed=on_wave,
                enforce_edge_semantics=_setting("enforce_edge_semantics", True),
            )

        # 5. Population impact, deduplicated across overlapping service areas.
        #    The cumulative set is published by the final wave, so it is read
        #    rather than re-accumulated -- that re-accumulation is exactly how
        #    cumulative and marginal sets got conflated downstream.
        all_failed_ids = (
            set(waves[-1].get("cumulative_failed_node_ids", []))
            if waves
            else set(initial_failures)
        )
        pop_impact = calculate_population_impact(all_failed_ids, G)

        # 6. Save results to Postgres. Marginal sets are disjoint by
        #    construction (a node is latched on failure and never re-enters), so
        #    summing them is the true total rather than a cumulative overcount.
        total_failed = len(all_failed_ids)
        
        sim.waves = waves
        sim.total_failed = total_failed
        sim.population_affected_estimate = pop_impact["population_affected_estimate"]
        # Persist the uncapped total too: when both a baseline and an
        # intervention saturate the study-area cap, the capped figure is
        # identical for each and a real improvement would be invisible.
        sim.raw_population_affected = pop_impact["raw_population_affected"]
        sim.deduplicated_population_affected = pop_impact["deduplicated_population_affected"]
        sim.study_area_population_cap = pop_impact["study_area_population_cap"]
        sim.is_population_capped = pop_impact["is_population_capped"]
        sim.has_unresolved_overlap = pop_impact["has_unresolved_overlap"]
        sim.cascade_stabilized = stabilized
        sim.global_efficiency_before = eff_before
        sim.global_efficiency_after = eff_after
        sim.status = "completed"
        sim.completed_at = datetime.now(UTC)
        
        # If part of a scenario, link the result back to the scenario
        if scenario_id and scenario:
            scenario.cached_result_id = sim.id
            
        db.commit()
        
        # Publish completion event
        get_redis_client().publish(f"sim_{simulation_id}", json.dumps({"status": "completed"}))
        
    except Exception:
        logger.exception("simulation %s failed", simulation_id)
        # A failure in the final db.commit() above leaves the session's
        # transaction in a state SQLAlchemy requires rolling back before any
        # further query — without this, the recovery query below raises
        # PendingRollbackError and the run is left stuck at status="running"
        # instead of being marked "failed". Matches the pattern already used
        # in app.services.ingestion's own except-then-rollback path.
        db.rollback()
        sim = db.query(SimulationResult).filter(SimulationResult.id == simulation_id).first()
        if sim:
            sim.status = "failed"
            sim.error_message = "Simulation execution failed. Consult server logs with the simulation ID."
            db.commit()
        get_redis_client().publish(f"sim_{simulation_id}", json.dumps({"status": "failed"}))
        raise
    finally:
        db.close()
