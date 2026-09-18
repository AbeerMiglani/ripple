"""
Ripple — FastAPI Application.

Entry point: uvicorn app.main:app --reload
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app import models as _models  # noqa: F401  # Registers ORM mappers before any relationship is resolved
from app.api.networks import router as networks_router
from app.api.scenarios import router as scenarios_router
from app.api.simulations import router as simulations_router
from app.api.ws import router as ws_router
from app.config import settings
from app.db.neo4j import close_neo4j_driver, verify_neo4j_connection
from app.db.postgres import verify_postgres_connection
from app.db.redis import verify_redis_connection
from app.logging_config import REQUEST_ID_HEADER, add_correlation_id, configure_logging

# Before anything else logs a line, including the lifespan startup checks
# below -- every app.* logger propagates to the root logger this configures.
configure_logging()

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Schema creation is performed by the explicit Alembic migration job. Do
    # not start an API process that can return misleading partial availability.
    #
    # Postgres and Redis are hard dependencies -- nothing in the API can serve
    # a meaningful response without them. Neo4j is soft: it is read by exactly
    # one feature, criticality centrality, and calculate_centrality (see
    # app.services.analytics) already falls back to an in-process NetworkX
    # computation when Neo4j is unreachable. Refusing to start the whole
    # process over that one degradable feature meant a Neo4j hiccup took down
    # simulations, scenarios and everything else that never touches it.
    required_health = {
        "postgres": verify_postgres_connection(),
        "redis": verify_redis_connection(),
    }
    unavailable = [name for name, healthy in required_health.items() if not healthy]
    if unavailable:
        logger.error("startup dependency check failed: %s", ", ".join(unavailable))
        raise RuntimeError(f"required services unavailable: {', '.join(unavailable)}")
    if not verify_neo4j_connection():
        logger.warning(
            "neo4j unreachable at startup; centrality will fall back to NetworkX "
            "until it recovers -- see GET /health for live status"
        )
    yield
    # Shutdown
    close_neo4j_driver()


app = FastAPI(
    title="Ripple API",
    description="Cascading Failure Simulation Platform",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None if settings.environment == "production" else "/docs",
    redoc_url=None if settings.environment == "production" else "/redoc",
)

# CORS config
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    # Browsers hide non-"simple" response headers from JS on a cross-origin
    # request unless the server explicitly exposes them. The bundled frontend
    # never needs this (Vite proxies /api same-origin, in dev and in the
    # containerized demo alike), but a direct cross-origin API consumer
    # otherwise cannot read the pagination headers networks.py sets.
    expose_headers=["X-Total-Count", "X-Has-More", REQUEST_ID_HEADER],
)
app.middleware("http")(add_correlation_id)

app.include_router(networks_router, prefix="/api")
app.include_router(simulations_router, prefix="/api")
app.include_router(scenarios_router, prefix="/api")
app.include_router(ws_router, prefix="/api")


@app.get("/health/live")
def liveness_check():
    """
    Liveness probe: the process is up and serving requests.

    Does not touch Postgres/Neo4j/Redis — unlike /health, this never fails
    because a downstream datastore is briefly unreachable, which is the
    behavior an orchestrator's liveness check (should this process be
    restarted?) wants, as distinct from readiness (should it receive
    traffic?).
    """
    return {"status": "ok"}


@app.get("/health")
def health_check():
    """
    Readiness check.

    Returns connectivity status for each backing service.
    """
    postgres_ok = verify_postgres_connection()
    neo4j_ok = verify_neo4j_connection()
    redis_ok = verify_redis_connection()
    healthy = postgres_ok and neo4j_ok and redis_ok

    payload = {
        "status": "ok" if healthy else "degraded",
        "services": {
            "postgres": "ok" if postgres_ok else "unreachable",
            "neo4j": "ok" if neo4j_ok else "unreachable",
            "redis": "ok" if redis_ok else "unreachable",
        },
    }
    if not healthy:
        raise HTTPException(status_code=503, detail=payload)
    return payload
