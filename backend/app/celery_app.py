"""
Celery application instance.

Shared between the backend (for .delay() calls) and the worker process.
"""

from celery import Celery
from celery.signals import setup_logging

from app.config import settings
from app.logging_config import configure_logging

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


@setup_logging.connect
def _use_app_logging_config(**_kwargs):
    """Replace Celery's own default logging setup with the app's.

    Connecting anything to this signal tells Celery to skip its own default
    configuration entirely and trust the receiver instead -- otherwise the
    worker process logs in Celery's own plain-text format, only the API
    process would emit structured JSON, and a task's logs would carry no
    correlation id at all.
    """
    configure_logging()
