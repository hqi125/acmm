from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class validation_issue:
    severity: str
    code: str
    path: str
    message: str


@dataclass(frozen=True)
class validation_result:
    errors: tuple[validation_issue, ...]
    warnings: tuple[validation_issue, ...]

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0


def split_issues(
    issues: Iterable[validation_issue],
) -> validation_result:
    errors = tuple(issue for issue in issues if issue.severity == "error")
    warnings = tuple(issue for issue in issues if issue.severity == "warning")

    return validation_result(
        errors=errors,
        warnings=warnings,
    )
