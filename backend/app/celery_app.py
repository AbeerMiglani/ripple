"""
Celery application instance.

Shared between the backend (for .delay() calls) and the worker process.
"""

from celery import Celery

from app.config import settings

celery_app = Celery(
    "ripple",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_time_limit=120,
    task_soft_time_limit=100,
    worker_prefetch_multiplier=1,
    task_publish_retry=True,
    broker_connection_retry_on_startup=True,
)

# Auto-discover task modules (looks for tasks.py by default)
# So we explicitly include runner.py
celery_app.conf.update(
    include=["app.simulation.runner"]
)
celery_app.autodiscover_tasks(["app.simulation"])
