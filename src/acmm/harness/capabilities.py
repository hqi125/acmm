from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class harness_capabilities:
    evaluators: frozenset[str]
    fixtures: frozenset[str]
    evidence_channels: frozenset[str]
