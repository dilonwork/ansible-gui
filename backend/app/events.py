"""Cross-process event bus for job events.

The Celery worker runs in a different process than the web server, so live
events can't travel through in-memory queues alone. Every event goes three ways:

1. persisted to the job's ``events`` JSON column (durable history + WS replay),
2. pushed to local ``ws_queues`` (same-process fast path: eager mode / tests),
3. published to Redis pub/sub (best-effort); the web process runs a forwarder
   thread that pushes those into local ``ws_queues`` for live WebSocket clients.

If Redis is unreachable, (1) and (2) still work; only cross-process live
push degrades (and without Redis, Celery can't run jobs anyway).
"""
import json
import logging
import os
import threading
import time

log = logging.getLogger("drydock.events")

CHANNEL = "drydock:job-events"

ws_queues: dict[str, list] = {}  # job_id -> [asyncio.Queue] (web process only)

_loop = None
_redis = None
_redis_failed_at = 0.0
_RETRY_COOLDOWN = 10.0


def register_loop(loop) -> None:
    global _loop
    _loop = loop


def get_redis():
    """Return a Redis client, or None if unreachable (throttled retries)."""
    global _redis, _redis_failed_at
    if _redis is not None:
        return _redis
    if time.time() - _redis_failed_at < _RETRY_COOLDOWN:
        return None
    try:
        import redis
        client = redis.Redis.from_url(
            os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
            socket_connect_timeout=2, socket_timeout=2,
        )
        client.ping()
        _redis = client
        return client
    except Exception as e:
        _redis_failed_at = time.time()
        log.warning("redis unavailable: %s", e)
        return None


def drop_redis() -> None:
    global _redis
    _redis = None


def _push_local(job_id: str, event: dict) -> None:
    if _loop is None:
        return
    for q in ws_queues.get(job_id, []):
        _loop.call_soon_threadsafe(q.put_nowait, event)


def emit_event(job_id: str, event: dict) -> None:
    from .db import session_scope
    from .models import Job
    with session_scope() as s:
        job = s.get(Job, job_id)
        if job:
            job.events = (job.events or []) + [event]
    _push_local(job_id, event)
    r = get_redis()
    if r is not None:
        try:
            r.publish(CHANNEL, json.dumps({"job_id": job_id, "event": event}))
        except Exception as e:
            log.warning("redis publish failed: %s", e)
            drop_redis()


def start_event_forwarder() -> None:
    """Daemon thread: forward Redis pub/sub messages into local ws_queues."""
    def _run():
        r = get_redis()
        if r is None:
            log.warning("event forwarder not started: redis unreachable")
            return
        try:
            ps = r.pubsub()
            ps.subscribe(CHANNEL)
            log.info("event forwarder subscribed to %s", CHANNEL)
            for msg in ps.listen():
                if msg.get("type") != "message":
                    continue
                try:
                    payload = json.loads(msg["data"])
                    _push_local(payload["job_id"], payload["event"])
                except Exception:
                    log.exception("bad event payload from redis")
        except Exception as e:
            log.warning("event forwarder stopped: %s", e)
            drop_redis()

    threading.Thread(target=_run, daemon=True, name="event-forwarder").start()
