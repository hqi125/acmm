from pathlib import Path

import acmm_infra.runtime_lifecycle as lifecycle_module
from acmm_infra.subject_managers import subject_manager_context


class _fake_docker:
    def __init__(self, running: bool) -> None:
        self.running = running

    def container_running(self) -> bool:
        return self.running


class _fake_manager:
    def __init__(self, running: bool) -> None:
        self.docker = _fake_docker(running)
        self.starts = 0
        self.stops = 0

    def up(self, *, timeout_seconds: float = 120.0) -> None:
        assert timeout_seconds > 0
        self.starts += 1
        self.docker.running = True

    def down(self) -> None:
        self.stops += 1
        self.docker.running = False


def test_runtime_lease_starts_and_releases_only_acmm_owned_runtime(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manager = _fake_manager(running=False)
    monkeypatch.setattr(
        lifecycle_module,
        "build_deployed_subject_manager",
        lambda *args, **kwargs: subject_manager_context(
            infrastructure_subject="hermes",
            experiment_id="example_v1",
            manager=manager,
        ),
    )

    lease = lifecycle_module.discover_runtime_lease(
        tmp_path,
        tmp_path / "runtime",
        "hermes",
    )

    assert lease.initially_running is False
    assert lease.started_by_acmm is False
    assert lease.ensure_started(timeout_seconds=30.0) is True
    assert lease.started_by_acmm is True
    assert manager.starts == 1

    assert lease.release() is True
    assert lease.started_by_acmm is False
    assert manager.stops == 1


def test_runtime_lease_preserves_preexisting_running_runtime(
    monkeypatch,
    tmp_path: Path,
) -> None:
    manager = _fake_manager(running=True)
    monkeypatch.setattr(
        lifecycle_module,
        "build_deployed_subject_manager",
        lambda *args, **kwargs: subject_manager_context(
            infrastructure_subject="hermes",
            experiment_id="example_v1",
            manager=manager,
        ),
    )

    lease = lifecycle_module.discover_runtime_lease(
        tmp_path,
        tmp_path / "runtime",
        "hermes",
    )

    assert lease.initially_running is True
    assert lease.ensure_started() is False
    assert lease.started_by_acmm is False
    assert lease.release() is False
    assert manager.starts == 0
    assert manager.stops == 0
