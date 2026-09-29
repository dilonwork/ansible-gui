"""Cron helpers for schedules: validation, human preview, next occurrence."""
import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cron_descriptor import get_description
from croniter import croniter, CroniterBadCronError


def validate_cron(cron: str) -> None:
    """Raises ValueError on invalid cron."""
    try:
        if not croniter.is_valid(cron):
            raise ValueError(f"invalid cron expression: {cron!r}")
    except (CroniterBadCronError, ValueError) as e:
        raise ValueError(str(e))


def validate_timezone(tz: str) -> None:
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"unknown timezone: {tz!r}")


def describe_cron(cron: str) -> str:
    try:
        return get_description(cron)
    except Exception:
        return cron


def _aware(after: float, tz: str) -> datetime.datetime:
    return datetime.datetime.fromtimestamp(
        after, tz=datetime.timezone.utc).astimezone(ZoneInfo(tz))


def next_occurrence(cron: str, tz: str, after: float) -> float:
    """Next cron fire strictly after `after` (UTC epoch in/out)."""
    nxt = croniter(cron, _aware(after, tz)).get_next(datetime.datetime)
    return nxt.astimezone(datetime.timezone.utc).timestamp()


def preview_next(cron: str, tz: str, after: float, n: int = 3) -> list[float]:
    out: list[float] = []
    t = after
    for _ in range(n):
        t = next_occurrence(cron, tz, t)
        out.append(t)
    return out


def count_missed(cron: str, tz: str, next_run_at: float, now: float,
                 limit: int = 1000) -> tuple[list[float], float]:
    """Walk occurrences from next_run_at up to now.

    Returns (missed_occurrences, next_future_occurrence). missed includes the
    latest due occurrence (the one that should fire now); the caller fires at
    most one catch-up run for it and records the rest as missed.
    """
    missed: list[float] = []
    nxt = next_run_at
    while nxt <= now and len(missed) < limit:
        missed.append(nxt)
        nxt = next_occurrence(cron, tz, nxt)
    return missed, nxt
