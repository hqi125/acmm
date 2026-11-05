from argparse import Namespace
from pathlib import Path

import pytest

from acmm_infra.subjects.agent_zero.cli import agent_zero_cli
from acmm_infra.subjects.hermes.cli import hermes_cli
from acmm_infra.subjects.open_webui.cli import open_webui_cli
from experiment_package.support import install_test_package, write_package_context


ROOT = Path(__file__).resolve().parents[2]
SUBJECTS = (
    ("hermes_gpt_oss120b", hermes_cli()),
    ("open_webui_gpt_oss120b", open_webui_cli()),
    ("agent_zero_gpt_oss120b", agent_zero_cli()),
)


def _args(runtime_root: Path) -> Namespace:
    return Namespace(repo_root=str(ROOT), runtime_root=str(runtime_root))


@pytest.mark.parametrize(("subject_id", "cli"), SUBJECTS)
def test_passive_context_reconstructs_package_backed_experiment(
    tmp_path: Path,
    monkeypatch,
    subject_id: str,
    cli,
) -> None:
    package, runtime_root = install_test_package(tmp_path, monkeypatch)
    write_package_context(package, runtime_root, subject_id)

    _, _, _, _, manager = cli._deployed_context(_args(runtime_root))

    assert manager.experiment_id == package.experiment_id
    assert manager.deployment_provenance is not None
    assert manager.deployment_provenance.package.package_sha256 == package.package_sha256


def test_passive_context_uses_baseline_when_no_experiment_is_active(
    tmp_path: Path,
) -> None:
    for _, cli in SUBJECTS:
        _, _, _, _, manager = cli._deployed_context(_args(tmp_path))
        assert manager.experiment_id is None


