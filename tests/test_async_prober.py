import io
from unittest.mock import patch

from devops_toolkit.async_prober import (
    CircuitBreaker,
    CircuitState,
    probe_many,
    summarize_outcomes,
)


class _FakeResponse(io.BytesIO):
    def __init__(self, status=200):
        super().__init__(b"ok")
        self._status = status

    def getcode(self):
        return self._status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# --- CircuitBreaker state machine -------------------------------------------------


def test_breaker_starts_closed_and_allows_requests():
    breaker = CircuitBreaker()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.allow_request(now=0) is True


def test_breaker_trips_open_after_threshold_failures():
    breaker = CircuitBreaker(failure_threshold=3)
    for _ in range(3):
        breaker.record_failure(now=0)
    assert breaker.state == CircuitState.OPEN
    assert breaker.allow_request(now=0) is False


def test_breaker_half_opens_after_reset_timeout():
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=10)
    breaker.record_failure(now=0)
    assert breaker.allow_request(now=5) is False
    assert breaker.allow_request(now=10) is True
    assert breaker.state == CircuitState.HALF_OPEN


def test_breaker_closes_on_success_after_half_open():
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=10)
    breaker.record_failure(now=0)
    breaker.allow_request(now=10)  # transitions to HALF_OPEN
    breaker.record_success()
    assert breaker.state == CircuitState.CLOSED
    assert breaker.consecutive_failures == 0


def test_breaker_reopens_on_failure_during_half_open():
    breaker = CircuitBreaker(failure_threshold=1, reset_timeout_seconds=10)
    breaker.record_failure(now=0)
    breaker.allow_request(now=10)  # -> HALF_OPEN
    breaker.record_failure(now=10)
    assert breaker.state == CircuitState.OPEN
    assert breaker.opened_at == 10


# --- probe_many (asyncio) ----------------------------------------------------------


def test_probe_many_marks_healthy_urls():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(200)):
        outcomes = probe_many(["https://a.example.com", "https://b.example.com"], retries=0)
    assert all(o.result is not None and o.result.healthy for o in outcomes)
    assert summarize_outcomes(outcomes) == {"healthy": 2, "unhealthy": 0, "skipped": 0}


def test_probe_many_skips_open_circuit():
    breaker = CircuitBreaker(failure_threshold=1)
    breaker.record_failure(now=time_now())  # pre-trip the breaker for this host
    breakers = {"https://down.example.com": breaker}

    with patch("urllib.request.urlopen", return_value=_FakeResponse(200)):
        outcomes = probe_many(["https://down.example.com"], breakers=breakers, retries=0)

    assert outcomes[0].skipped is True
    assert summarize_outcomes(outcomes) == {"healthy": 0, "unhealthy": 0, "skipped": 1}


def time_now():
    import time

    return time.monotonic()
