"""HTTP health checking with retries — the "monitoring checks" half of the
Adform operational-automation work. Uses only the standard library so it
runs anywhere Python 3.9+ is installed, no pip install required.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field


@dataclass
class HealthResult:
    url: str
    healthy: bool
    status_code: int | None = None
    latency_ms: float | None = None
    error: str | None = None
    attempts: int = 0


def check_http(
    url: str,
    timeout: float = 5.0,
    expected_status: int = 200,
    retries: int = 2,
    backoff_seconds: float = 1.0,
) -> HealthResult:
    """Check a single HTTP(S) endpoint, retrying transient failures.

    Returns a HealthResult rather than raising, so a batch of checks can
    run to completion and report on all of them.
    """
    last_error: str | None = None
    for attempt in range(1, retries + 2):  # retries=2 -> up to 3 attempts
        start = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "devops-toolkit-healthcheck/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as response:
                latency_ms = (time.monotonic() - start) * 1000
                status = response.getcode()
                return HealthResult(
                    url=url,
                    healthy=(status == expected_status),
                    status_code=status,
                    latency_ms=round(latency_ms, 1),
                    attempts=attempt,
                )
        except urllib.error.HTTPError as exc:
            latency_ms = (time.monotonic() - start) * 1000
            return HealthResult(
                url=url,
                healthy=(exc.code == expected_status),
                status_code=exc.code,
                latency_ms=round(latency_ms, 1),
                attempts=attempt,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = str(exc)
            if attempt <= retries:
                time.sleep(backoff_seconds * attempt)  # linear backoff
            continue

    return HealthResult(url=url, healthy=False, error=last_error, attempts=retries + 1)


def check_many(urls: list[str], **kwargs) -> list[HealthResult]:
    return [check_http(url, **kwargs) for url in urls]


def summarize(results: list[HealthResult]) -> tuple[int, int]:
    """Returns (healthy_count, unhealthy_count)."""
    healthy = sum(1 for r in results if r.healthy)
    return healthy, len(results) - healthy
