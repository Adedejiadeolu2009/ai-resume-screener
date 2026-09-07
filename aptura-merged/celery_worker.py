import os

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

broker_url = (
    os.getenv("CELERY_BROKER_URL")
    or os.getenv("REDIS_URL")
    or "redis://localhost:6379/0"
)
result_backend = (
    os.getenv("CELERY_RESULT_BACKEND")
    or os.getenv("REDIS_URL")
    or "redis://localhost:6379/1"
)

celery_app = Celery(
    "aptura",
    broker=broker_url,
    backend=result_backend,
    include=["screening_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_track_started=True,
)
