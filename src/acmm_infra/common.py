from __future__ import annotations

from dataclasses import dataclass


class infrastructure_error(RuntimeError):
    """Raised when infrastructure state cannot be safely managed."""


@dataclass(frozen=True)
class doctor_check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class doctor_report:
    checks: tuple[doctor_check, ...]

    @property
    def is_ready(self) -> bool:
        return all(check.ok for check in self.checks)
