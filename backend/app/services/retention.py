"""Deletes old simulation runs and scenarios.

A public deployment accepts runs from anyone, so its tables would otherwise
grow without bound. The seeded network itself is never touched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import CursorResult, delete
from sqlalchemy.orm import Session

from app.models.network import Scenario, SimulationResult


def delete_expired(db: Session, *, older_than: timedelta, now: datetime | None = None) -> dict[str, int]:
    """Delete runs and scenarios created before ``now - older_than``.

    Runs and scenarios refer to each other only through ON DELETE SET NULL
    keys, so neither order can fail: a newer run of an expired scenario keeps
    its result and loses only the link, and the same holds the other way.
    """
    cutoff = (now or datetime.now(UTC)) - older_than
    # A DELETE's result is a CursorResult, which carries the row count.
    runs = cast(CursorResult, db.execute(delete(SimulationResult).where(SimulationResult.created_at < cutoff)))
    scenarios = cast(CursorResult, db.execute(delete(Scenario).where(Scenario.created_at < cutoff)))
    db.commit()
    return {
        "simulations_deleted": runs.rowcount or 0,
        "scenarios_deleted": scenarios.rowcount or 0,
    }
