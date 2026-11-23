"""Endpoints for a scheduler, not for visitors."""

from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db.postgres import get_db
from app.security import enforce_rate_limit
from app.services.retention import delete_expired


def require_cron_secret(request: Request) -> None:
    """Accept only the scheduler, which sends ``Authorization: Bearer <CRON_SECRET>``.

    Vercel Cron sends exactly that header when the project defines
    CRON_SECRET. With no secret configured the endpoint does not exist.
    """
    secret = settings.cron_secret
    if secret is None or not secret.get_secret_value():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    presented = request.headers.get("authorization", "")
    expected = f"Bearer {secret.get_secret_value()}"
    if not secrets.compare_digest(presented.encode(), expected.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid scheduler credentials")


router = APIRouter(
    prefix="/internal",
    tags=["Internal"],
    include_in_schema=False,
    # Limiter first, for the same reason as the public routers: it has to see
    # every request, including the ones that then fail authentication.
    dependencies=[Depends(enforce_rate_limit), Depends(require_cron_secret)],
)


@router.get("/cleanup")
def cleanup_expired(db: Session = Depends(get_db)) -> dict[str, int]:
    """Delete runs and scenarios older than RETENTION_DAYS. Called daily."""
    return delete_expired(db, older_than=timedelta(days=settings.retention_days))
