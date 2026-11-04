from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


_OLLAMA_USAGE_ENDPOINT = "https://ollama.com/api/usage"
_OLLAMA_USAGE_RANGE = "24h"
_OLLAMA_USAGE_SCOPE = "self"
_OLLAMA_USAGE_GROUP_BY = "model"
_OLLAMA_USAGE_TIMEOUT_SECONDS = 10.0
_OLLAMA_USAGE_SETTLE_INTERVALS_SECONDS = (1.0, 2.0, 4.0, 8.0)
_METRIC_FIELDS = (
    "request_count",
    "usage_usd",
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
)


class execution_provider_usage_monitor(Protocol):
    """Best-effort provider usage observation around one execution."""

    def begin(self) -> None:
        ...

    def finish(self) -> Mapping[str, object]:
        ...


@dataclass(frozen=True)
class _usage_snapshot:
    status: str
    sampled_at: str
    account: Mapping[str, int | float | None] | None
    models: Mapping[str, Mapping[str, int | float | None]]
    error: str | None = None


class ollama_cloud_usage_monitor:
    """Capture account-level Ollama Cloud usage before and after an execution."""

    def __init__(
        self,
        model_names: tuple[str, ...],
        *,
        api_key: str | None = None,
        opener: Callable[..., Any] = urlopen,
        now: Callable[[], datetime] | None = None,
        timeout_seconds: float = _OLLAMA_USAGE_TIMEOUT_SECONDS,
        expected_minimum_request_count: int | None = None,
        settle_intervals_seconds: tuple[float, ...] = (
            _OLLAMA_USAGE_SETTLE_INTERVALS_SECONDS
        ),
        wait: Callable[[float], None] = time.sleep,
    ) -> None:
        normalized = tuple(dict.fromkeys(name for name in model_names if name))
        if not normalized:
            raise ValueError("Ollama Cloud usage monitoring requires at least one model.")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero.")
        if (
            expected_minimum_request_count is not None
            and expected_minimum_request_count < 0
        ):
            raise ValueError(
                "expected_minimum_request_count must be non-negative when provided."
            )
        if any(interval < 0 for interval in settle_intervals_seconds):
            raise ValueError("settle intervals must be non-negative.")

        self._model_names = normalized
        self._api_key = api_key if api_key is not None else os.getenv("OLLAMA_API_KEY")
        self._opener = opener
        self._now = now if now is not None else lambda: datetime.now(timezone.utc)
        self._timeout_seconds = timeout_seconds
        self._expected_minimum_request_count = expected_minimum_request_count
        self._settle_intervals_seconds = settle_intervals_seconds
        self._wait = wait
        self._before: _usage_snapshot | None = None

    def begin(self) -> None:
        self._before = self._sample()

    def finish(self) -> Mapping[str, object]:
        before = self._before
        if before is None:
            before = self._unavailable_snapshot(
                "provider usage monitoring did not capture a pre-execution snapshot"
            )

        initial_after = self._sample()
        after = initial_after
        waits: list[float] = []
        sample_count = 1
        target = self._expected_minimum_request_count

        if target is not None and target > 0:
            for interval in self._settle_intervals_seconds:
                if self._request_delta_reaches_target(before, after, target):
                    break
                if after.status != "available":
                    break
                self._wait(interval)
                waits.append(interval)
                after = self._sample()
                sample_count += 1

        report = self._build_report(before, after)
        target_reached = (
            self._request_delta_reaches_target(before, after, target)
            if target is not None
            else None
        )
        report["settling"] = {
            "expected_minimum_request_count": target,
            "sample_count": sample_count,
            "waits_seconds": waits,
            "target_reached": target_reached,
        }
        if sample_count > 1:
            report["after_initial"] = _snapshot_mapping(initial_after)

        return {
            "ollama_cloud": report,
        }

    def _request_delta_reaches_target(
        self,
        before: _usage_snapshot,
        after: _usage_snapshot,
        target: int | None,
    ) -> bool:
        if target is None:
            return True
        delta = _observed_model_request_delta(
            before,
            after,
            self._model_names,
        )
        return delta is not None and delta >= target

    def _sample(self) -> _usage_snapshot:
        sampled_at = _format_timestamp(self._now())
        if not self._api_key:
            return _usage_snapshot(
                status="unavailable",
                sampled_at=sampled_at,
                account=None,
                models={},
                error="OLLAMA_API_KEY is not set",
            )

        query = urlencode(
            {
                "range": _OLLAMA_USAGE_RANGE,
                "scope": _OLLAMA_USAGE_SCOPE,
                "group_by": _OLLAMA_USAGE_GROUP_BY,
            }
        )
        request = Request(
            f"{_OLLAMA_USAGE_ENDPOINT}?{query}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            method="GET",
        )

        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                payload = json.load(response)
        except HTTPError as exc:
            return _usage_snapshot(
                status="unavailable",
                sampled_at=sampled_at,
                account=None,
                models={},
                error=f"HTTP {exc.code} while fetching Ollama Cloud usage",
            )
        except (URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            return _usage_snapshot(
                status="unavailable",
                sampled_at=sampled_at,
                account=None,
                models={},
                error=f"Could not fetch Ollama Cloud usage: {exc}",
            )

        try:
            account, models = _normalize_usage_payload(payload)
        except ValueError as exc:
            return _usage_snapshot(
                status="unavailable",
                sampled_at=sampled_at,
                account=None,
                models={},
                error=f"Invalid Ollama Cloud usage response: {exc}",
            )

        return _usage_snapshot(
            status="available",
            sampled_at=sampled_at,
            account=account,
            models=models,
        )

    def _unavailable_snapshot(self, error: str) -> _usage_snapshot:
        return _usage_snapshot(
            status="unavailable",
            sampled_at=_format_timestamp(self._now()),
            account=None,
            models={},
            error=error,
        )

    def _build_report(
        self,
        before: _usage_snapshot,
        after: _usage_snapshot,
    ) -> dict[str, object]:
        if before.status == "available" and after.status == "available":
            status = "available"
        elif before.status == "available" or after.status == "available":
            status = "partial"
        else:
            status = "unavailable"

        model_reports: dict[str, object] = {}
        for model_name in self._model_names:
            aliases = _ollama_usage_model_aliases(model_name)
            before_provider_model, before_metrics = _first_model_metrics(
                before.models,
                aliases,
            )
            after_provider_model, after_metrics = _first_model_metrics(
                after.models,
                aliases,
            )
            provider_model = after_provider_model or before_provider_model
            model_reports[model_name] = {
                "provider_model": provider_model,
                "before": before_metrics,
                "after": after_metrics,
                "delta": _metric_delta(before_metrics, after_metrics),
                "complete": before_metrics is not None and after_metrics is not None,
            }

        return {
            "status": status,
            "source": _OLLAMA_USAGE_ENDPOINT,
            "range": _OLLAMA_USAGE_RANGE,
            "scope": _OLLAMA_USAGE_SCOPE,
            "group_by": _OLLAMA_USAGE_GROUP_BY,
            "models_observed": list(self._model_names),
            "before": _snapshot_mapping(before),
            "after": _snapshot_mapping(after),
            "account_delta": _metric_delta(before.account, after.account),
            "models": model_reports,
            "caveat": (
                "Usage is sampled at account scope and may include concurrent "
                "activity outside this ACMM execution. Per-model groups may be "
                "omitted by the provider when they fall outside the returned group limit."
            ),
        }



def _observed_model_request_delta(
    before: _usage_snapshot,
    after: _usage_snapshot,
    model_names: tuple[str, ...],
) -> int | None:
    total = 0
    for model_name in model_names:
        aliases = _ollama_usage_model_aliases(model_name)
        _, before_metrics = _first_model_metrics(before.models, aliases)
        _, after_metrics = _first_model_metrics(after.models, aliases)
        if before_metrics is None or after_metrics is None:
            return None
        before_count = before_metrics.get("request_count")
        after_count = after_metrics.get("request_count")
        if (
            not isinstance(before_count, int)
            or isinstance(before_count, bool)
            or not isinstance(after_count, int)
            or isinstance(after_count, bool)
        ):
            return None
        total += after_count - before_count
    return total


def summarize_ollama_cloud_request_count(
    provider_usage: Mapping[str, object] | None,
) -> tuple[int | None, str]:
    """Return a human-readable request-count result for execution summaries."""

    if not isinstance(provider_usage, Mapping):
        return None, "telemetry was not recorded"
    report = provider_usage.get("ollama_cloud")
    if not isinstance(report, Mapping):
        return None, "Ollama Cloud telemetry was not recorded"

    status = report.get("status")
    if status == "unavailable":
        error = _usage_report_error(report)
        return None, error or "Ollama Cloud telemetry is unavailable"

    observed = report.get("models_observed")
    models = report.get("models")
    if isinstance(observed, list) and isinstance(models, Mapping):
        total = 0
        complete = True
        for model_name in observed:
            if not isinstance(model_name, str):
                complete = False
                break
            model_report = models.get(model_name)
            if not isinstance(model_report, Mapping) or not model_report.get("complete"):
                complete = False
                break
            delta = model_report.get("delta")
            if not isinstance(delta, Mapping):
                complete = False
                break
            request_count = delta.get("request_count")
            if not isinstance(request_count, int) or isinstance(request_count, bool):
                complete = False
                break
            total += request_count
        if complete and observed:
            detail = "model-scoped delta"
            settling = report.get("settling")
            if isinstance(settling, Mapping):
                target_reached = settling.get("target_reached")
                if target_reached is False:
                    detail += "; provider settle target not reached"
            if total == 0:
                detail += "; provider snapshots did not advance"
            return total, detail

    account_delta = report.get("account_delta")
    if isinstance(account_delta, Mapping):
        request_count = account_delta.get("request_count")
        if isinstance(request_count, int) and not isinstance(request_count, bool):
            detail = "account-scope fallback; model groups incomplete"
            if request_count == 0:
                detail += "; provider snapshots did not advance"
            return request_count, detail

    error = _usage_report_error(report)
    return None, error or "request-count delta is unavailable"


def _usage_report_error(report: Mapping[str, object]) -> str | None:
    for key in ("after", "before"):
        snapshot = report.get(key)
        if not isinstance(snapshot, Mapping):
            continue
        error = snapshot.get("error")
        if isinstance(error, str) and error.strip():
            return error.strip()
    return None


def _ollama_usage_model_aliases(model_name: str) -> tuple[str, ...]:
    aliases = [model_name]
    if model_name.endswith("-cloud"):
        aliases.append(model_name[: -len("-cloud")])
    return tuple(dict.fromkeys(aliases))


def _first_model_metrics(
    models: Mapping[str, Mapping[str, int | float | None]],
    aliases: tuple[str, ...],
) -> tuple[str | None, Mapping[str, int | float | None] | None]:
    for alias in aliases:
        metrics = models.get(alias)
        if metrics is not None:
            return alias, metrics
    return None, None

def _normalize_usage_payload(
    payload: object,
) -> tuple[dict[str, int | float | None], dict[str, dict[str, int | float | None]]]:
    if not isinstance(payload, dict):
        raise ValueError("response must be a JSON object")

    totals = payload.get("totals")
    if not isinstance(totals, dict):
        raise ValueError("response.totals must be an object")

    account = _normalize_metrics(totals)
    groups = totals.get("groups", [])
    if not isinstance(groups, list):
        raise ValueError("response.totals.groups must be an array when present")

    models: dict[str, dict[str, int | float | None]] = {}
    for group in groups:
        if not isinstance(group, dict):
            continue
        model_name = group.get("model")
        if not isinstance(model_name, str) or not model_name:
            continue
        models[model_name] = _normalize_metrics(group)

    return account, models


def _normalize_metrics(data: Mapping[str, object]) -> dict[str, int | float | None]:
    request_count = data.get("request_count")
    if not isinstance(request_count, int) or isinstance(request_count, bool):
        raise ValueError("usage metrics require integer request_count")

    metrics: dict[str, int | float | None] = {"request_count": request_count}
    for field in _METRIC_FIELDS[1:]:
        value = data.get(field)
        if value is None:
            metrics[field] = None
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            metrics[field] = value
        else:
            raise ValueError(f"usage metric {field} must be numeric when present")
    return metrics


def _metric_delta(
    before: Mapping[str, int | float | None] | None,
    after: Mapping[str, int | float | None] | None,
) -> dict[str, int | float | None] | None:
    if before is None or after is None:
        return None

    delta: dict[str, int | float | None] = {}
    for field in _METRIC_FIELDS:
        before_value = before.get(field)
        after_value = after.get(field)
        if before_value is None or after_value is None:
            delta[field] = None
        else:
            delta[field] = after_value - before_value
    return delta


def _snapshot_mapping(snapshot: _usage_snapshot) -> dict[str, object]:
    result: dict[str, object] = {
        "status": snapshot.status,
        "sampled_at": snapshot.sampled_at,
        "account": snapshot.account,
        "models": dict(snapshot.models),
    }
    if snapshot.error is not None:
        result["error"] = snapshot.error
    return result


def _format_timestamp(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat(timespec="milliseconds").replace("+00:00", "Z")
