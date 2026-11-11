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
    assert "blocked:         0" in status.out


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
    assert context is not None
    assert tuple(run.run_index for run in context.selected_runs) == (0,)
