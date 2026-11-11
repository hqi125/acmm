from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from acmm.campaign.model import (
    build_campaign_manifest,
    build_campaign_preview,
    campaign_error,
    create_campaign,
    load_campaign,
)
from experiment_package.support import install_test_package


def test_campaign_design_digest_is_stable_across_campaign_instances(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)

    first = build_campaign_manifest(
        package,
        campaign_id="campaign-a",
        now=lambda: datetime(2026, 11, 11, tzinfo=timezone.utc),
    )
    second = build_campaign_manifest(
        package,
        campaign_id="campaign-b",
        now=lambda: datetime(2026, 11, 12, tzinfo=timezone.utc),
    )

    assert first["campaign"]["design_digest"] == second["campaign"]["design_digest"]
    assert [run["run_id"] for run in first["runs"]] != [
        run["run_id"] for run in second["runs"]
    ]


def test_campaign_logical_run_ids_are_deterministic_within_campaign(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)

    first = build_campaign_manifest(package, campaign_id="campaign-a")
    second = build_campaign_manifest(package, campaign_id="campaign-a")

    assert [run["run_id"] for run in first["runs"]] == [
        run["run_id"] for run in second["runs"]
    ]


def test_campaign_preview_does_not_require_campaign_identity(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)

    preview = build_campaign_preview(package)

    assert preview.experiment_id == package.experiment_id
    assert preview.package_sha256 == package.package_sha256
    assert preview.run_count > 0
    assert len(preview.design_digest) == 64


def test_campaign_persistence_round_trip_validates_frozen_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    generated_root = tmp_path / "generated"

    created = create_campaign(
        package,
        generated_root,
        campaign_id="campaign-a",
    )
    loaded = load_campaign("campaign-a", generated_root)

    assert loaded.path == created.path
    assert loaded.manifest == created.manifest
    assert (created.path / "contract.yaml").read_bytes() == package.contract_path.read_bytes()


def test_campaign_load_rejects_modified_frozen_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, _ = install_test_package(tmp_path, monkeypatch)
    generated_root = tmp_path / "generated"
    created = create_campaign(
        package,
        generated_root,
        campaign_id="campaign-a",
    )
    (created.path / "contract.yaml").write_text("modified\n", encoding="utf-8")

    with pytest.raises(campaign_error, match="Contract SHA-256 mismatch"):
        load_campaign("campaign-a", generated_root)


def test_campaign_can_restore_its_frozen_package_revision_after_runtime_store_removal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package, runtime_root = install_test_package(tmp_path, monkeypatch)
    generated_root = tmp_path / "generated"
    created = create_campaign(
        package,
        generated_root,
        campaign_id="campaign-a",
    )

    import shutil

    from acmm.campaign.model import ensure_campaign_package_installed

    shutil.rmtree(package.root)
    restored = ensure_campaign_package_installed(created, runtime_root)

    assert restored.package_sha256 == package.package_sha256
    assert restored.root.is_dir()
    assert restored.contract_sha256 == package.contract_sha256
