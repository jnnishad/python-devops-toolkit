"""Concurrent multi-endpoint prober with a per-host circuit breaker.

`health_check.py` checks endpoints one at a time, which doesn't scale past
a handful of URLs and will happily keep hammering a host that's already
down. This module runs checks concurrently with asyncio and wraps each
host in its own circuit breaker (closed -> open -> half-open) so a
persistently failing endpoint stops being probed at full frequency and
the rest of the batch isn't slowed down waiting on its retries.

Stdlib only: concurrency comes from asyncio.gather() + loop.run_in_executor()
around the existing synchronous urllib call, not from an extra HTTP
dependency. The state machine and scheduling logic are pure/sync and unit
tested without any real network access or event loop timing.
"""

from __future__ import annotations

import asyncio
import enum
import time
from dataclasses import dataclass, field

from devops_toolkit.health_check import HealthResult, check_http


class CircuitState(enum.Enum):
    CLOSED = "closed"        # normal operation, requests flow through
    OPEN = "open"             # tripped, requests are short-circuited
    HALF_OPEN = "half_open"   # cooldown elapsed, letting one probe through


@dataclass
class CircuitBreaker:
    """Per-host failure tracker.

    Trips to OPEN after `failure_threshold` consecutive failures. Once
    `reset_timeout_seconds` has elapsed since it tripped, the next call
    is allowed through as a HALF_OPEN probe; success closes the circuit
    again, failure re-opens it and restarts the cooldown.
    """

    failure_threshold: int = 3
    reset_timeout_seconds: float = 30.0
    state: CircuitState = field(default=CircuitState.CLOSED)
    consecutive_failures: int = 0
    opened_at: float | None = None

    def allow_request(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            assert self.opened_at is not None
            if now - self.opened_at >= self.reset_timeout_seconds:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
        # HALF_OPEN: allow exactly one in-flight probe at a time
        return True

    def record_success(self) -> None:
        self.state = CircuitState.CLOSED
        self.consecutive_failures = 0
        self.opened_at = None

    def record_failure(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self.consecutive_failures += 1
        if self.state == CircuitState.HALF_OPEN or self.consecutive_failures >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = now


@dataclass
class ProbeOutcome:
    url: str
    result: HealthResult | None
    circuit_state: CircuitState
    skipped: bool  # True when the circuit breaker short-circuited this probe


async def _probe_one(
    url: str,
    breaker: CircuitBreaker,
    semaphore: asyncio.Semaphore,
    loop: asyncio.AbstractEventLoop,
    **check_kwargs,
) -> ProbeOutcome:
    if not breaker.allow_request():
        return ProbeOutcome(url=url, result=None, circuit_state=breaker.state, skipped=True)

    async with semaphore:
        result = await loop.run_in_executor(None, lambda: check_http(url, **check_kwargs))

    if result.healthy:
        breaker.record_success()
    else:
        breaker.record_failure()
    return ProbeOutcome(url=url, result=result, circuit_state=breaker.state, skipped=False)


async def probe_many_async(
    urls: list[str],
    breakers: dict[str, CircuitBreaker] | None = None,
    max_concurrency: int = 20,
    **check_kwargs,
) -> list[ProbeOutcome]:
    """Probe every URL concurrently (bounded by `max_concurrency`),
    honoring a circuit breaker per host so a dead endpoint doesn't keep
    consuming a worker slot on every call.
    """
    breakers = breakers if breakers is not None else {url: CircuitBreaker() for url in urls}
    for url in urls:
        breakers.setdefault(url, CircuitBreaker())

    semaphore = asyncio.Semaphore(max_concurrency)
    loop = asyncio.get_event_loop()
    tasks = [_probe_one(url, breakers[url], semaphore, loop, **check_kwargs) for url in urls]
    return await asyncio.gather(*tasks)


def probe_many(
    urls: list[str],
    breakers: dict[str, CircuitBreaker] | None = None,
    max_concurrency: int = 20,
    **check_kwargs,
) -> list[ProbeOutcome]:
    """Synchronous entrypoint for the CLI/scripts that don't want to
    manage an event loop themselves."""
    return asyncio.run(probe_many_async(urls, breakers, max_concurrency, **check_kwargs))


def summarize_outcomes(outcomes: list[ProbeOutcome]) -> dict[str, int]:
    counts = {"healthy": 0, "unhealthy": 0, "skipped": 0}
    for outcome in outcomes:
        if outcome.skipped:
            counts["skipped"] += 1
        elif outcome.result and outcome.result.healthy:
            counts["healthy"] += 1
        else:
            counts["unhealthy"] += 1
    return counts
