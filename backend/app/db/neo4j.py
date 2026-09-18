"""
Neo4j driver wrapper.

During simulations, Neo4j is READ-ONLY — the worker pulls graph data into
an in-memory NetworkX graph and never writes back to Neo4j. This module
enforces that convention by providing separate read-only helpers.
"""

from contextlib import contextmanager

from neo4j import GraphDatabase

from app.config import settings

_driver = None


def get_neo4j_driver():
    """Lazy-initialize and return the Neo4j driver singleton."""
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
    return _driver


@contextmanager
def neo4j_session(*, write: bool = False):
    """
    Context manager for a Neo4j session.

    Args:
        write: If False (default), opens a READ access session.
               Simulation code must always use write=False.
    """
    driver = get_neo4j_driver()
    access_mode = "WRITE" if write else "READ"
    session = driver.session(default_access_mode=access_mode)
    try:
        yield session
    finally:
        session.close()


def close_neo4j_driver():
    """Close the driver — call on app shutdown."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def verify_neo4j_connection() -> bool:
    """Ping Neo4j to verify connectivity. Returns True if healthy."""
    try:
        driver = get_neo4j_driver()
        driver.verify_connectivity()
        return True
    except Exception:
        return False
