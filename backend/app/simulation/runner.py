from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

try:
    from datetime import UTC
except ImportError:
    UTC = timezone.utc

from celery import shared_task
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.config import settings
from app.db.postgres import SessionLocal
from app.db.redis import get_redis_client
from app.logging_config import correlation_id_var
from app.models.network import Edge, Node, Scenario, SimulationResult
from app.services.graph_build import build_graph
from app.simulation.cascade import run_cascade
from app.simulation.isolation import isolated_graph
from app.simulation.population import calculate_population_impact
# Re-exported: this module was its home, and existing callers import it from here.
from app.simulation.scenario import apply_scenario_modifications

__all__ = ["TRANSIENT_INFRA_ERRORS", "apply_scenario_modifications", "run_simulation_task"]

logger = logging.getLogger(__name__)

#: A dropped Postgres connection or a Redis blip is not a verdict on the
#: simulation -- rerunning the exact same deterministic computation a moment
#: later either succeeds or hits the same infra problem again. Anything else
#: (a bad scenario, a bug in the cascade engine) is not: retrying a
#: deterministic error just reproduces it three times slower.
TRANSIENT_INFRA_ERRORS = (OperationalError, RedisConnectionError, RedisTimeoutError)


def _mark_failed(db: Session, simulation_id: str, message: str) -> None:
    """Record a simulation as failed and notify anyone watching it.

    A failure here leaves the session's transaction in a state SQLAlchemy
    requires rolling back before any further query -- without that, this
    recovery query itself raises PendingRollbackError and the run is left
    stuck at status="running" instead of being marked "failed". Matches the
    pattern already used in app.services.ingestion's own except-then-rollback
    path.
    """
    db.rollback()
    sim = db.query(SimulationResult).filter(SimulationResult.id == simulation_id).first()
    if sim:
        sim.status = "failed"
        sim.error_message = message
        db.commit()
    try:
        get_redis_client().publish(f"sim_{simulation_id}", json.dumps({"status": "failed"}))
    except Exception:
        # The DB row -- the durable record every caller actually polls -- is
        # already correct at this point. A missed pub/sub push is a degraded
        # notice, not a lost result, and is not worth failing the handler
        # over -- especially since a Redis blip is often the very reason
        # this path was reached.
        logger.warning("could not publish failure notice for simulation %s", simulation_id, exc_info=True)


@shared_task(bind=True, max_retries=3)
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
    # Every log line this attempt produces carries the same tag a retried
    # attempt's logs also will, so grepping one simulation's logs works
    # across attempts and across the API/worker process boundary alike --
    # even though the API side's own request id is a different value, both
    # name the same simulation_id in the message text.
    correlation_token = correlation_id_var.set(f"sim:{simulation_id}")
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
                max_waves=settings.max_cascade_waves,
                on_wave_completed=on_wave,
                enforce_edge_semantics=settings.enforce_edge_semantics,
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
        
    except TRANSIENT_INFRA_ERRORS as exc:
        db.rollback()
        if self.request.retries < self.max_retries:
            # Exponential backoff, capped at 30s: a dropped connection is
            # usually back within a couple of seconds, and this bounds how
            # long a truly wedged datastore keeps a worker slot occupied
            # retrying. The row is left at status="running" -- a caller
            # polling it sees an in-progress run, not a false failure that
            # then mysteriously un-fails itself a few seconds later.
            countdown = min(2**self.request.retries, 30)
            logger.warning(
                "transient infra error running simulation %s (attempt %s/%s), retrying in %ss",
                simulation_id, self.request.retries + 1, self.max_retries + 1, countdown,
                exc_info=exc,
            )
            raise self.retry(exc=exc, countdown=countdown)
        logger.exception("simulation %s failed after exhausting retries on transient infra errors", simulation_id)
        _mark_failed(
            db,
            simulation_id,
            "Simulation execution failed after repeated transient infrastructure errors. "
            "Consult server logs with the simulation ID.",
        )
        raise
    except Exception:
        logger.exception("simulation %s failed", simulation_id)
        _mark_failed(db, simulation_id, "Simulation execution failed. Consult server logs with the simulation ID.")
        raise
    finally:
        db.close()
        correlation_id_var.reset(correlation_token)
