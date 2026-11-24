"""
Celery application for Module 4 distributed worker pool.
Broker/backend default to Redis; disable via CELERY_ENABLED=false for local inline dispatch.
"""

from __future__ import annotations

import os

from celery import Celery

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER = os.getenv("CELERY_BROKER_URL", REDIS_URL)
CELERY_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL)

celery_app = Celery(
    "wolf_module_4",
    broker=CELERY_BROKER,
    backend=CELERY_BACKEND,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="wolf.orchestrator",
)


@celery_app.task(name="wolf.dispatch_task", bind=True, max_retries=0)
def dispatch_task_async(self, task_id: str) -> dict:
    """Celery worker entrypoint — executes Module 4 dispatch for an enqueued task."""
    from services.module_4_orchestrator.dispatcher import orchestrator

    return orchestrator.execute_task_dispatch(task_id)


@celery_app.task(name="wolf.ping")
def ping() -> dict:
    return {"status": "ok", "service": "module_4_celery"}
