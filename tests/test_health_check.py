import io
import urllib.error
from unittest.mock import patch

from devops_toolkit.health_check import check_http, check_many, summarize


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


def test_check_http_success():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(200)):
        result = check_http("https://example.com/healthz")
    assert result.healthy is True
    assert result.status_code == 200
    assert result.attempts == 1


def test_check_http_wrong_status_is_unhealthy():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(200)):
        result = check_http("https://example.com/healthz", expected_status=204)
    assert result.healthy is False
    assert result.status_code == 200


def test_check_http_retries_on_connection_error_then_succeeds():
    calls = {"n": 0}

    def side_effect(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            raise urllib.error.URLError("connection refused")
        return _FakeResponse(200)

    with patch("urllib.request.urlopen", side_effect=side_effect):
        result = check_http("https://example.com/healthz", retries=2, backoff_seconds=0)

    assert result.healthy is True
    assert result.attempts == 2


def test_check_http_gives_up_after_retries_exhausted():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
        result = check_http("https://example.com/healthz", retries=1, backoff_seconds=0)
    assert result.healthy is False
    assert result.attempts == 2
    assert "down" in result.error


def test_check_http_handles_http_error_status():
    err = urllib.error.HTTPError("https://example.com", 503, "Service Unavailable", hdrs=None, fp=None)
    with patch("urllib.request.urlopen", side_effect=err):
        result = check_http("https://example.com/healthz")
    assert result.healthy is False
    assert result.status_code == 503


def test_summarize_counts_healthy_and_unhealthy():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(200)):
        results = check_many(["https://a.example.com", "https://b.example.com"])
    healthy, unhealthy = summarize(results)
    assert healthy == 2
    assert unhealthy == 0
