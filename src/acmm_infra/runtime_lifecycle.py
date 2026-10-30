from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from acmm_infra.common import infrastructure_error
from acmm_infra.docker import docker_error
from acmm_infra.profiles.common import profile_error
from acmm_infra.subject_managers import (
    build_deployed_subject_manager,
    subject_manager_context,
)


@dataclass
class subject_runtime_lease:
    infrastructure_subject: str
    context: subject_manager_context
    initially_running: bool
    started_by_acmm: bool = False

    @classmethod
    def discover(
        cls,
        repo_root: str | Path,
        runtime_root: str | Path,
        infrastructure_subject: str,
        llm_baseline_id: str | None = None,
    ) -> subject_runtime_lease:
        context = build_deployed_subject_manager(
            repo_root,
            runtime_root,
            infrastructure_subject,
            llm_baseline_id,
        )
        return cls(
            infrastructure_subject=infrastructure_subject,
            context=context,
            initially_running=context.manager.docker.container_running(),
        )

    @property
    def is_running(self) -> bool:
        return self.context.manager.docker.container_running()

    def ensure_started(self, *, timeout_seconds: float = 120.0) -> bool:
        if self.is_running:
            return False

        try:
            self.context.manager.up(timeout_seconds=timeout_seconds)
        except (
            infrastructure_error,
            profile_error,
            docker_error,
            OSError,
            ValueError,
        ):
            if self.context.manager.docker.container_running():
                self.started_by_acmm = True
                try:
                    self.release()
                except (
                    infrastructure_error,
                    profile_error,
                    docker_error,
                    OSError,
                    ValueError,
                ):
                    pass
            raise

        self.started_by_acmm = True
        return True

    def release(self) -> bool:
        if not self.started_by_acmm:
            return False

        self.context.manager.down()
        self.started_by_acmm = False
        return True


def discover_runtime_lease(
    repo_root: str | Path,
    runtime_root: str | Path,
    infrastructure_subject: str,
    llm_baseline_id: str | None = None,
) -> subject_runtime_lease:
    try:
        return subject_runtime_lease.discover(
            repo_root,
            runtime_root,
            infrastructure_subject,
            llm_baseline_id,
        )
    except (infrastructure_error, profile_error, OSError, ValueError):
        raise
