from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json

from acmm.runtime.provider_usage import (
    ollama_cloud_usage_monitor,
    summarize_ollama_cloud_request_count,
)


class _response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False


def _clock():
    current = datetime(2026, 11, 2, 20, 0, 0, tzinfo=timezone.utc)

    def now():
        nonlocal current
        value = current
        current += timedelta(seconds=1)
        return value

    return now


def test_ollama_usage_monitor_computes_account_and_model_deltas() -> None:
    payloads = [
        {
            "totals": {
                "request_count": 100,
                "usage_usd": 1.0,
                "input_tokens": 1000,
                "cached_input_tokens": 200,
                "output_tokens": 300,
                "groups": [
                    {
                        "model": "gpt-oss:120b",
                        "request_count": 80,
                        "usage_usd": 0.8,
                        "input_tokens": 800,
                        "cached_input_tokens": 160,
                        "output_tokens": 240,
                    }
                ],
            }
        },
        {
            "totals": {
                "request_count": 107,
                "usage_usd": 1.08,
                "input_tokens": 1600,
                "cached_input_tokens": 260,
                "output_tokens": 410,
                "groups": [
                    {
                        "model": "gpt-oss:120b",
                        "request_count": 87,
                        "usage_usd": 0.88,
                        "input_tokens": 1400,
                        "cached_input_tokens": 220,
                        "output_tokens": 350,
                    }
                ],
            }
        },
    ]
    requests = []

    def opener(request, *, timeout):
        requests.append((request, timeout))
        return _response(json.dumps(payloads.pop(0)).encode("utf-8"))

    monitor = ollama_cloud_usage_monitor(
        ("gpt-oss:120b-cloud",),
        api_key="test-key",
        opener=opener,
        now=_clock(),
    )
    monitor.begin()
    report = monitor.finish()["ollama_cloud"]

    assert report["status"] == "available"
    assert report["account_delta"]["request_count"] == 7
    assert report["account_delta"]["input_tokens"] == 600
    model = report["models"]["gpt-oss:120b-cloud"]
    assert model["complete"] is True
    assert model["provider_model"] == "gpt-oss:120b"
    assert model["delta"]["request_count"] == 7
    assert len(requests) == 2
    assert requests[0][0].get_header("Authorization") == "Bearer test-key"
    assert "group_by=model" in requests[0][0].full_url


def test_ollama_usage_monitor_is_unavailable_without_api_key() -> None:
    monitor = ollama_cloud_usage_monitor(
        ("gpt-oss:120b-cloud",),
        api_key="",
        now=_clock(),
    )
    monitor.begin()
    report = monitor.finish()["ollama_cloud"]

    assert report["status"] == "unavailable"
    assert report["before"]["error"] == "OLLAMA_API_KEY is not set"
    assert report["after"]["error"] == "OLLAMA_API_KEY is not set"
    assert report["account_delta"] is None


def test_ollama_usage_monitor_marks_missing_model_group_incomplete() -> None:
    payload = {
        "totals": {
            "request_count": 10,
            "groups": [],
        }
    }

    def opener(request, *, timeout):
        del request, timeout
        return _response(json.dumps(payload).encode("utf-8"))

    monitor = ollama_cloud_usage_monitor(
        ("gpt-oss:120b-cloud",),
        api_key="test-key",
        opener=opener,
        now=_clock(),
    )
    monitor.begin()
    report = monitor.finish()["ollama_cloud"]

    model = report["models"]["gpt-oss:120b-cloud"]
    assert model["complete"] is False
    assert model["delta"] is None


def test_ollama_request_count_summary_prefers_model_scoped_delta() -> None:
    provider_usage = {
        "ollama_cloud": {
            "status": "available",
            "models_observed": ["gpt-oss:120b-cloud"],
            "models": {
                "gpt-oss:120b-cloud": {
                    "complete": True,
                    "delta": {"request_count": 4},
                }
            },
            "account_delta": {"request_count": 9},
        }
    }

    value, detail = summarize_ollama_cloud_request_count(provider_usage)

    assert value == 4
    assert detail == "model-scoped delta"


def test_ollama_request_count_summary_reports_missing_key() -> None:
    provider_usage = {
        "ollama_cloud": {
            "status": "unavailable",
            "before": {"error": "OLLAMA_API_KEY is not set"},
            "after": {"error": "OLLAMA_API_KEY is not set"},
        }
    }

    value, detail = summarize_ollama_cloud_request_count(provider_usage)

    assert value is None
    assert detail == "OLLAMA_API_KEY is not set"


def test_ollama_usage_monitor_settles_until_expected_request_floor_is_visible() -> None:
    payloads = []
    for request_count in (100, 101, 102, 103):
        payloads.append(
            {
                "totals": {
                    "request_count": request_count,
                    "groups": [
                        {
                            "model": "gpt-oss:120b",
                            "request_count": request_count,
                        }
                    ],
                }
            }
        )

    waits = []

    def opener(request, *, timeout):
        del request, timeout
        return _response(json.dumps(payloads.pop(0)).encode("utf-8"))

    monitor = ollama_cloud_usage_monitor(
        ("gpt-oss:120b-cloud",),
        api_key="test-key",
        opener=opener,
        now=_clock(),
        expected_minimum_request_count=3,
        settle_intervals_seconds=(1.0, 5.0, 15.0),
        wait=waits.append,
    )
    monitor.begin()
    report = monitor.finish()["ollama_cloud"]

    assert report["models"]["gpt-oss:120b-cloud"]["delta"]["request_count"] == 3
    assert report["after_initial"]["account"]["request_count"] == 101
    assert report["after"]["account"]["request_count"] == 103
    assert report["settling"] == {
        "expected_minimum_request_count": 3,
        "sample_count": 3,
        "waits_seconds": [1.0, 5.0],
        "target_reached": True,
    }
    assert waits == [1.0, 5.0]


def test_ollama_usage_monitor_records_when_settle_target_is_not_reached() -> None:
    payloads = []
    for request_count in (100, 101, 102):
        payloads.append(
            {
                "totals": {
                    "request_count": request_count,
                    "groups": [
                        {
                            "model": "gpt-oss:120b",
                            "request_count": request_count,
                        }
                    ],
                }
            }
        )

    def opener(request, *, timeout):
        del request, timeout
        return _response(json.dumps(payloads.pop(0)).encode("utf-8"))

    monitor = ollama_cloud_usage_monitor(
        ("gpt-oss:120b-cloud",),
        api_key="test-key",
        opener=opener,
        now=_clock(),
        expected_minimum_request_count=3,
        settle_intervals_seconds=(1.0,),
        wait=lambda seconds: None,
    )
    monitor.begin()
    provider_usage = monitor.finish()
    report = provider_usage["ollama_cloud"]

    assert report["models"]["gpt-oss:120b-cloud"]["delta"]["request_count"] == 2
    assert report["settling"]["target_reached"] is False

    value, detail = summarize_ollama_cloud_request_count(provider_usage)

    assert value == 2
    assert detail == "model-scoped delta; provider settle target not reached"
