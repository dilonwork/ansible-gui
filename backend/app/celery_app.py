"""Celery application: durable background execution for jobs.

Broker and result backend are Redis (REDIS_URL). Tasks ack late so a dead
worker redelivers the job instead of losing it.

Tests set CELERY_EAGER=1, which runs tasks inline in the web process
(no Redis needed).
"""
import os

from celery import Celery

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

celery = Celery("drydock", broker=REDIS_URL, backend=REDIS_URL)
celery.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)

if os.environ.get("CELERY_EAGER") == "1":
    celery.conf.task_always_eager = True

# ensure the task is registered when a worker boots from this app module
# (import at the end: tasks.py imports `celery` from here)
from . import tasks  # noqa: F401,E402
