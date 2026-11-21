from __future__ import annotations

from pathlib import Path

from acmm.cli import _build_parser
from experiment_package.support import install_test_package


EXPERIMENT_ID = "package_smoke_v1"


def test_campaign_create_preview_does_not_persist_campaign(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    generated_root = tmp_path / "generated"

    parser = _build_parser()
    args = parser.parse_args(
        [
            "campaign",
            "create",
            EXPERIMENT_ID,
            "--preview",
            "--generated-root",
            str(generated_root),
        ]
    )

    assert args.handler(args) == 0
    captured = capsys.readouterr()
    assert "Campaign preview:" in captured.out
    assert package.package_sha256 in captured.out
    assert "No campaign created (--preview)." in captured.out
    assert not generated_root.exists()


def test_campaign_create_then_status_round_trip(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    generated_root = tmp_path / "generated"

    parser = _build_parser()
    create_args = parser.parse_args(
        [
            "campaign",
            "create",
            EXPERIMENT_ID,
            "--generated-root",
            str(generated_root),
        ]
    )
    assert create_args.handler(create_args) == 0
    created = capsys.readouterr()
    assert "Campaign created:" in created.out
    assert package.package_sha256 in created.out

    campaign_dirs = tuple((generated_root / EXPERIMENT_ID).glob("campaign_*"))
    assert len(campaign_dirs) == 1
    campaign_id = campaign_dirs[0].name.removeprefix("campaign_")

    status_args = parser.parse_args(
        [
            "campaign",
            "status",
            campaign_id,
            "--generated-root",
            str(generated_root),
        ]
    )
    assert status_args.handler(status_args) == 0
    status = capsys.readouterr()
    assert "Campaign status:" in status.out
    assert f"id:              {campaign_id}" in status.out
    assert "observed:        0" in status.out


def test_campaign_execute_uses_frozen_campaign_selection(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    generated_root = tmp_path / "generated"

    from acmm.campaign.model import create_campaign

    artifact = create_campaign(
        package,
        generated_root,
        campaign_id="campaign-a",
    )
    captured_context = []

    def fake_run(args, *, campaign_context=None):
        captured_context.append((args, campaign_context))
        return 0

    monkeypatch.setattr("acmm.cli._run_experiment", fake_run)

    parser = _build_parser()
    args = parser.parse_args(
        [
            "campaign",
            "execute",
            artifact.campaign_id,
            "--run-index",
            "0",
            "--generated-root",
            str(generated_root),
        ]
    )

    assert args.handler(args) == 0
    output = capsys.readouterr().out
    assert "Campaign execution selection:" in output
    assert "eligible pending:  1" in output
    assert len(captured_context) == 1
    run_args, context = captured_context[0]
    assert run_args.experiment == f"{artifact.experiment_id}@{package.package_sha256}"
    assert run_args.max_attempts_per_run == 2
    assert context is not None
    assert tuple(run.run_index for run in context.selected_runs) == (0,)


def test_campaign_resume_replays_only_pending_source_selection(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    generated_root = tmp_path / "generated"

    from datetime import datetime, timezone
    from types import SimpleNamespace

    from acmm.campaign.execution import (
        create_campaign_execution_context,
        finalize_campaign_execution,
        frozen_campaign_plan,
        initialize_campaign_execution,
    )
    from acmm.campaign.model import create_campaign

    artifact = create_campaign(
        package,
        generated_root,
        campaign_id="campaign-a",
    )
    runs = frozen_campaign_plan(artifact).runs[:2]
    source = create_campaign_execution_context(
        artifact,
        runs,
        execution_id="execution-source",
    )
    source.session_dir.mkdir(parents=True)
    initialize_campaign_execution(source)
    finalize_campaign_execution(
        source,
        SimpleNamespace(
            ended_at=datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc),
            slots=(
                SimpleNamespace(planned=runs[0], is_resolved=True, attempts=()),
                SimpleNamespace(planned=runs[1], is_resolved=False, attempts=()),
            ),
        ),
        status="completed",
    )

    captured_context = []

    def fake_run(args, *, campaign_context=None):
        captured_context.append((args, campaign_context))
        return 0

    monkeypatch.setattr("acmm.cli._run_experiment", fake_run)

    parser = _build_parser()
    args = parser.parse_args(
        [
            "campaign",
            "resume",
            artifact.campaign_id,
            "--generated-root",
            str(generated_root),
        ]
    )

    assert args.handler(args) == 0
    output = capsys.readouterr().out
    assert "Campaign resume selection:" in output
    assert "source execution:  execution-source" in output
    assert len(captured_context) == 1
    run_args, context = captured_context[0]
    assert run_args.max_attempts_per_run == 2
    assert context is not None
    assert context.resumed_from_execution_id == "execution-source"
    assert tuple(run.run_index for run in context.selected_runs) == (
        runs[1].run_index,
    )


def test_campaign_execute_preview_is_side_effect_free(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    generated_root = tmp_path / "generated"
    from acmm.campaign.model import create_campaign

    artifact = create_campaign(
        package,
        generated_root,
        campaign_id="campaign-preview",
    )

    def forbidden_reconcile(_artifact):
        raise AssertionError("preview must not reconcile campaign state")

    def forbidden_run(*_args, **_kwargs):
        raise AssertionError("preview must not invoke experiment execution")

    monkeypatch.setattr("acmm.campaign.cli.reconcile_campaign", forbidden_reconcile)
    monkeypatch.setattr("acmm.cli._run_experiment", forbidden_run)

    parser = _build_parser()
    args = parser.parse_args(
        [
            "campaign",
            "execute",
            artifact.campaign_id,
            "--preview",
            "--run-index",
            "0-1",
            "--generated-root",
            str(generated_root),
        ]
    )

    assert args.handler(args) == 0
    output = capsys.readouterr().out
    assert "Campaign execution preview:" in output
    assert "matched:           2" in output
    assert "eligible pending:  2" in output
    assert f"package:          {package.package_sha256}" in output
    assert "run indices:      0-1" in output
    assert "No execution started (--preview)." in output
    assert not (artifact.path / "executions").exists()
