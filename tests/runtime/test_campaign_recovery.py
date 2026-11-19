from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from acmm.campaign.execution import (
    create_campaign_execution_context,
    frozen_campaign_plan,
    initialize_campaign_execution,
    inspect_campaign_status,
)
from acmm.campaign.model import campaign_error, create_campaign
from acmm.campaign.recovery import reconcile_campaign
from acmm.runtime.interfaces import runtime_execution_recovery_result
from experiment_package.support import install_test_package


def _unclean_campaign(tmp_path: Path, monkeypatch):
    package, _ = install_test_package(tmp_path, monkeypatch)
    artifact = create_campaign(
        package,
        tmp_path / "generated",
        campaign_id="campaign-a",
    )
    run = frozen_campaign_plan(artifact).runs[0]
    context = create_campaign_execution_context(
        artifact,
        (run,),
        execution_id="execution-a",
    )
    context.session_dir.mkdir(parents=True)
    journal = initialize_campaign_execution(context)
    journal.emit(
        "environment_prepare_started",
        {"run_index": run.run_index, "attempt": 1},
    )
    return artifact, context


def test_unclean_lifecycle_execution_requires_package_recovery_hook(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact, context = _unclean_campaign(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "acmm.campaign.recovery.load_campaign_package",
        lambda artifact: SimpleNamespace(runtime=object()),
    )
    monkeypatch.setattr(
        "acmm.campaign.recovery.load_staged_experiment_runtime",
        lambda package: SimpleNamespace(
            runtime=SimpleNamespace(lifecycle=object(), recovery=None)
        ),
    )

    with pytest.raises(campaign_error, match="has no recovery hook"):
        reconcile_campaign(artifact)

    manifest = json.loads(
        (context.session_dir / "campaign_execution.json").read_text(encoding="utf-8")
    )
    assert manifest["execution"]["status"] == "interrupted_unclean"
    assert manifest["recovery"]["status"] == "required"
    status = inspect_campaign_status(artifact)
    assert status.recovery.required == 1


def test_unclean_lifecycle_execution_uses_package_recovery_hook(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact, context = _unclean_campaign(tmp_path, monkeypatch)
    seen = []

    class _recovery:
        def reconcile(self, recovery_context):
            seen.append(recovery_context)
            return runtime_execution_recovery_result(
                safe_to_continue=True,
                changed=True,
                detail="residual external state retired",
                observations={"retired": 1},
            )

    monkeypatch.setattr(
        "acmm.campaign.recovery.load_campaign_package",
        lambda artifact: SimpleNamespace(runtime=object()),
    )
    monkeypatch.setattr(
        "acmm.campaign.recovery.load_staged_experiment_runtime",
        lambda package: SimpleNamespace(
            runtime=SimpleNamespace(lifecycle=object(), recovery=_recovery())
        ),
    )

    result = reconcile_campaign(artifact)

    assert result.locally_reconciled_execution_ids == ("execution-a",)
    assert result.externally_recovered_execution_ids == ("execution-a",)
    assert len(seen) == 1
    assert seen[0].execution_id == "execution-a"
    assert seen[0].session_dir == str(context.session_dir)
    manifest = json.loads(
        (context.session_dir / "campaign_execution.json").read_text(encoding="utf-8")
    )
    assert manifest["recovery"]["status"] == "recovered"
    assert manifest["recovery"]["changed"] is True
    assert manifest["recovery"]["observations"] == {"retired": 1}
    status = inspect_campaign_status(artifact)
    assert status.recovery.recovered == 1


def test_unclean_execution_without_runtime_lifecycle_needs_no_external_recovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact, context = _unclean_campaign(tmp_path, monkeypatch)

    monkeypatch.setattr(
        "acmm.campaign.recovery.load_campaign_package",
        lambda artifact: SimpleNamespace(runtime=object()),
    )
    monkeypatch.setattr(
        "acmm.campaign.recovery.load_staged_experiment_runtime",
        lambda package: SimpleNamespace(
            runtime=SimpleNamespace(lifecycle=None, recovery=None)
        ),
    )

    result = reconcile_campaign(artifact)

    assert result.recovery_not_required_execution_ids == ("execution-a",)
    manifest = json.loads(
        (context.session_dir / "campaign_execution.json").read_text(encoding="utf-8")
    )
    assert manifest["recovery"]["status"] == "not_required"

def test_recovery_rejects_invalid_hook_result_fields(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact, context = _unclean_campaign(tmp_path, monkeypatch)

    class _recovery:
        def reconcile(self, recovery_context):
            del recovery_context
            return runtime_execution_recovery_result(
                safe_to_continue=1,
                changed=False,
                detail="invalid boolean",
            )

    monkeypatch.setattr(
        "acmm.campaign.recovery.load_campaign_package",
        lambda artifact: SimpleNamespace(runtime=object()),
    )
    monkeypatch.setattr(
        "acmm.campaign.recovery.load_staged_experiment_runtime",
        lambda package: SimpleNamespace(
            runtime=SimpleNamespace(lifecycle=object(), recovery=_recovery())
        ),
    )

    with pytest.raises(campaign_error, match="safe_to_continue must be a boolean"):
        reconcile_campaign(artifact)

    manifest = json.loads(
        (context.session_dir / "campaign_execution.json").read_text(encoding="utf-8")
    )
    assert manifest["recovery"]["status"] == "failed"


def test_recovery_rejects_nonserializable_observations(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact, context = _unclean_campaign(tmp_path, monkeypatch)

    class _recovery:
        def reconcile(self, recovery_context):
            del recovery_context
            return runtime_execution_recovery_result(
                safe_to_continue=True,
                changed=False,
                detail="bad observations",
                observations={"bad": object()},
            )

    monkeypatch.setattr(
        "acmm.campaign.recovery.load_campaign_package",
        lambda artifact: SimpleNamespace(runtime=object()),
    )
    monkeypatch.setattr(
        "acmm.campaign.recovery.load_staged_experiment_runtime",
        lambda package: SimpleNamespace(
            runtime=SimpleNamespace(lifecycle=object(), recovery=_recovery())
        ),
    )

    with pytest.raises(campaign_error, match="observations must be JSON-serializable"):
        reconcile_campaign(artifact)

    manifest = json.loads(
        (context.session_dir / "campaign_execution.json").read_text(encoding="utf-8")
    )
    assert manifest["recovery"]["status"] == "failed"



def test_failed_execution_requires_recovery_before_campaign_can_continue(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact, context = _unclean_campaign(tmp_path, monkeypatch)
    (context.session_dir / "experiment_failure.yaml").write_text(
        "schema_version: '0.1'\nexecution:\n  status: failed\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "acmm.campaign.recovery.load_campaign_package",
        lambda artifact: SimpleNamespace(runtime=object()),
    )
    monkeypatch.setattr(
        "acmm.campaign.recovery.load_staged_experiment_runtime",
        lambda package: SimpleNamespace(
            runtime=SimpleNamespace(lifecycle=object(), recovery=None)
        ),
    )

    with pytest.raises(campaign_error, match="has no recovery hook"):
        reconcile_campaign(artifact)

    manifest = json.loads(
        (context.session_dir / "campaign_execution.json").read_text(encoding="utf-8")
    )
    assert manifest["execution"]["status"] == "failed"
    assert manifest["recovery"]["status"] == "required"


def test_typed_interruption_uses_package_recovery_hook_when_available(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact, context = _unclean_campaign(tmp_path, monkeypatch)
    (context.session_dir / "experiment_interruption.yaml").write_text(
        "schema_version: '0.1'\nexecution:\n  status: interrupted\n",
        encoding="utf-8",
    )
    seen = []

    class _recovery:
        def reconcile(self, recovery_context):
            seen.append(recovery_context.execution_id)
            return runtime_execution_recovery_result(
                safe_to_continue=True,
                changed=False,
                detail="typed interruption already clean",
            )

    monkeypatch.setattr(
        "acmm.campaign.recovery.load_campaign_package",
        lambda artifact: SimpleNamespace(runtime=object()),
    )
    monkeypatch.setattr(
        "acmm.campaign.recovery.load_staged_experiment_runtime",
        lambda package: SimpleNamespace(
            runtime=SimpleNamespace(lifecycle=object(), recovery=_recovery())
        ),
    )

    result = reconcile_campaign(artifact)

    assert seen == ["execution-a"]
    assert result.externally_recovered_execution_ids == ("execution-a",)
    manifest = json.loads(
        (context.session_dir / "campaign_execution.json").read_text(encoding="utf-8")
    )
    assert manifest["execution"]["status"] == "interrupted"
    assert manifest["recovery"]["status"] == "recovered"


def test_typed_interruption_without_recovery_hook_remains_resumable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact, context = _unclean_campaign(tmp_path, monkeypatch)
    (context.session_dir / "experiment_interruption.yaml").write_text(
        "schema_version: '0.1'\nexecution:\n  status: interrupted\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "acmm.campaign.recovery.load_campaign_package",
        lambda artifact: SimpleNamespace(runtime=object()),
    )
    monkeypatch.setattr(
        "acmm.campaign.recovery.load_staged_experiment_runtime",
        lambda package: SimpleNamespace(
            runtime=SimpleNamespace(lifecycle=object(), recovery=None)
        ),
    )

    result = reconcile_campaign(artifact)

    assert result.recovery_not_required_execution_ids == ("execution-a",)
    manifest = json.loads(
        (context.session_dir / "campaign_execution.json").read_text(encoding="utf-8")
    )
    assert manifest["execution"]["status"] == "interrupted"
    assert manifest["recovery"]["status"] == "not_required"


def test_campaign_recovery_contains_system_exit_and_persists_failed_recovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    artifact, context = _unclean_campaign(tmp_path, monkeypatch)

    class _recovery:
        def reconcile(self, recovery_context):
            del recovery_context
            raise SystemExit("synthetic recovery SystemExit")

    monkeypatch.setattr(
        "acmm.campaign.recovery.load_campaign_package",
        lambda artifact: SimpleNamespace(runtime=object()),
    )
    monkeypatch.setattr(
        "acmm.campaign.recovery.load_staged_experiment_runtime",
        lambda package: SimpleNamespace(
            runtime=SimpleNamespace(lifecycle=object(), recovery=_recovery())
        ),
    )

    with pytest.raises(campaign_error, match="synthetic recovery SystemExit"):
        reconcile_campaign(artifact)

    manifest = json.loads(
        (context.session_dir / "campaign_execution.json").read_text(encoding="utf-8")
    )
    assert manifest["recovery"]["status"] == "failed"
    assert manifest["recovery"]["detail"] == "synthetic recovery SystemExit"
