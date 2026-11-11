from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from acmm.experiment_package import (
    acquire_experiment_package,
    acquire_staged_integration_resources,
    apply_staged_integration_resources,
    doctor_staged_integration_resources,
    down_staged_integration_resources,
    compose_staged_experiment_profile_integrations,
    compose_staged_experiment_manager_integrations,
    experiment_integration_error,
    load_experiment_package,
    load_staged_experiment_integration,
    load_staged_experiment_integrations,
    restore_staged_integration_resources,
    remove_superseded_staged_integration_resources,
)
from acmm_infra.subjects.hermes.profile import load_hermes_profile
from acmm_infra.subjects.factory import build_package_subject_manager


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_SCHEMA = ROOT / "contracts" / "v0_1" / "contract.schema.json"
VALID_CONTRACT = ROOT / "tests" / "fixtures" / "contracts" / "valid" / "package_fixture.yaml"
EXPERIMENT_ID = "package_fixture_v1"


INTEGRATION_SOURCE = '''from dataclasses import replace

from acmm.integration_api import experiment_integration, subject_integration


class hermes_profile_integration:
    def __init__(self, context):
        self.context = context

    def compose_profile(self, context, profile):
        assert context.integration_id == self.context.integration_id
        assert context.preset == "test_preset_v1"
        assert context.configuration["reasoning_effort"] == "low"
        return replace(profile, reasoning_effort=context.configuration["reasoning_effort"])


def build_integration(context):
    return experiment_integration(
        subjects={
            "hermes": subject_integration(
                profile=hermes_profile_integration(context),
            )
        },
    )
'''


RESOURCE_INTEGRATION_SOURCE = r'''from acmm.integration_api import (
    experiment_integration,
    integration_check,
    integration_report,
)


class resource:
    def apply(self, context, *, timeout_seconds=120.0, progress=None):
        del timeout_seconds
        context.state_root.mkdir(parents=True, exist_ok=True)
        (context.state_root / "applied.txt").write_text("ready", encoding="utf-8")
        if progress is not None:
            progress("reconciled")
        return {"state_root": str(context.state_root), "running": self.is_running(context)}

    def status(self, context):
        return {
            "ready": (context.state_root / "applied.txt").is_file(),
            "running": self.is_running(context),
        }

    def is_running(self, context):
        return (context.state_root / "running.txt").is_file()

    def up(self, context, *, timeout_seconds=120.0, progress=None):
        del timeout_seconds
        (context.state_root / "running.txt").write_text("running", encoding="utf-8")
        if progress is not None:
            progress("started")

    def doctor(self, context):
        ready = (
            (context.state_root / "applied.txt").is_file()
            and self.is_running(context)
        )
        return integration_report(
            checks=(integration_check("resource", ready, "ready" if ready else "missing"),)
        )

    def down(self, context):
        marker = context.state_root / "running.txt"
        if marker.exists():
            marker.unlink()

    def remove(self, context):
        self.down(context)
        marker = context.state_root / "applied.txt"
        if marker.exists():
            marker.unlink()


def build_integration(context):
    del context
    return experiment_integration(shared_resource=resource())
'''


RESOURCE_REMOVE_MARKER_SOURCE = r'''from acmm.integration_api import experiment_integration


class resource:
    def apply(self, context, *, timeout_seconds=120.0, progress=None):
        del timeout_seconds, progress
        context.state_root.mkdir(parents=True, exist_ok=True)
        return {}

    def status(self, context):
        del context
        return {}

    def is_running(self, context):
        del context
        return False

    def up(self, context, *, timeout_seconds=120.0, progress=None):
        del context, timeout_seconds, progress

    def doctor(self, context):
        del context
        raise RuntimeError("not used")

    def down(self, context):
        del context

    def remove(self, context):
        (context.runtime_root / "old-resource-removed.txt").write_text(
            "removed", encoding="utf-8"
        )


def build_integration(context):
    del context
    return experiment_integration(shared_resource=resource())
'''


ADMISSION_INTEGRATION_SOURCE = '''from acmm.integration_api import experiment_integration, subject_integration


class runtime_admission:
    def __init__(self):
        self.events = []

    def admit(self, context, manager, payload):
        del manager
        self.events.append(("admit", context.infrastructure_subject, dict(payload)))
        return {"secret_handle": payload["secret"]}

    def revoke(self, context, manager, handle):
        del manager
        self.events.append(("revoke", context.infrastructure_subject, dict(handle)))
        return {"revoked": True}


_ADMISSION = runtime_admission()


def build_integration(context):
    del context
    return experiment_integration(
        subjects={
            "hermes": subject_integration(admission=_ADMISSION),
        },
    )
'''


MANAGER_INTEGRATION_SOURCE = '''from acmm.integration_api import (
    experiment_integration,
    subject_integration,
)


class manager_wrapper:
    def __init__(self, manager, integration_id):
        self._manager = manager
        self.integration_id = integration_id
        self.profile = manager.profile
        self.deployment_root = manager.deployment_root
        self.docker = manager.docker

    def apply(self, **kwargs):
        result = self._manager.apply(**kwargs)
        return {**result, "integration_id": self.integration_id}

    def status(self):
        return {**self._manager.status(), "integration_id": self.integration_id}

    def doctor(self):
        return self._manager.doctor()

    def up(self, **kwargs):
        return self._manager.up(**kwargs)

    def down(self):
        return self._manager.down()


class hermes_manager_integration:
    def compose_manager(self, context, manager):
        return manager_wrapper(manager, context.integration_id)


def build_integration(context):
    del context
    return experiment_integration(
        subjects={
            "hermes": subject_integration(
                manager=hermes_manager_integration(),
            )
        },
    )
'''


def _write_integration_package(
    root: Path,
    *,
    integration_source: str = INTEGRATION_SOURCE,
) -> None:
    root.mkdir(parents=True)
    shutil.copy2(VALID_CONTRACT, root / "experiment.yaml")
    (root / "experiment-package.yaml").write_text(
        'schema_version: "0.2"\n'
        f"experiment_id: {EXPERIMENT_ID}\n"
        "contract: experiment.yaml\n"
        "integrations:\n"
        "  - id: profile-test\n"
        '    api_version: "0.2"\n'
        "    root: integrations/profile-test\n"
        "    entrypoint: build_integration\n"
        "    preset: test_preset_v1\n"
        "    configuration:\n"
        "      reasoning_effort: low\n",
        encoding="utf-8",
    )
    integration_root = root / "integrations" / "profile-test"
    integration_root.mkdir(parents=True)
    (integration_root / "__init__.py").write_text(
        integration_source,
        encoding="utf-8",
    )


def _acquire(
    tmp_path: Path,
    *,
    integration_source: str = INTEGRATION_SOURCE,
):
    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_integration_package(source, integration_source=integration_source)
    package = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    return source, package, runtime_root


def test_package_schema_v0_2_loads_integration_component(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_integration_package(source)

    package = load_experiment_package(
        source,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    assert package.schema_version == "0.2"
    assert len(package.integrations) == 1
    component = package.integrations[0]
    assert component.integration_id == "profile-test"
    assert component.api_version == "0.2"
    assert component.preset == "test_preset_v1"
    assert component.configuration["reasoning_effort"] == "low"
    assert component.root == source.resolve() / "integrations/profile-test"
    assert len(component.sha256) == 64


def test_integration_activation_requires_staged_package(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_integration_package(source)
    package = load_experiment_package(
        source,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    with pytest.raises(experiment_integration_error, match="ACMM-staged"):
        load_staged_experiment_integration(package, "profile-test")


def test_integration_activation_detects_staged_tampering(tmp_path: Path) -> None:
    _, package, _ = _acquire(tmp_path)
    component = package.integrations[0]
    (component.root / "__init__.py").write_text(
        INTEGRATION_SOURCE + "\nVALUE = 2\n",
        encoding="utf-8",
    )

    with pytest.raises(experiment_integration_error, match="changed before integration"):
        load_staged_experiment_integration(package, "profile-test")


def test_integration_composes_subject_profile_from_staged_code(tmp_path: Path) -> None:
    source, package, _ = _acquire(tmp_path)
    shutil.rmtree(source)
    baseline = load_hermes_profile(ROOT)

    composed = compose_staged_experiment_profile_integrations(
        package,
        "hermes",
        baseline.llm_baseline_id,
        baseline,
    )

    assert composed.reasoning_effort == "low"
    assert baseline.reasoning_effort != composed.reasoning_effort


def test_package_subject_manager_applies_generic_integration(tmp_path: Path) -> None:
    _, package, runtime_root = _acquire(tmp_path)

    context = build_package_subject_manager(
        ROOT,
        runtime_root,
        "hermes",
        package,
    )

    assert context.manager.profile.reasoning_effort == "low"
    assert context.package_sha256 == package.package_sha256


def test_manager_integration_wraps_subject_manager(tmp_path: Path) -> None:
    _, package, runtime_root = _acquire(
        tmp_path,
        integration_source=MANAGER_INTEGRATION_SOURCE,
    )

    context = build_package_subject_manager(
        ROOT,
        runtime_root,
        "hermes",
        package,
    )

    assert context.manager.integration_id == "profile-test"
    assert context.manager.profile.llm_baseline_id == context.llm_baseline_id


def test_manager_integration_can_reuse_loaded_snapshot(tmp_path: Path) -> None:
    _, package, runtime_root = _acquire(
        tmp_path,
        integration_source=MANAGER_INTEGRATION_SOURCE,
    )
    loaded = load_staged_experiment_integrations(package)
    context = build_package_subject_manager(
        ROOT,
        runtime_root,
        "hermes",
        package,
    )

    recomposed = compose_staged_experiment_manager_integrations(
        package,
        "hermes",
        context.llm_baseline_id,
        context.manager._manager,
        loaded_integrations=loaded,
    )

    assert recomposed.integration_id == "profile-test"


def test_integration_must_preserve_subject_profile_type(tmp_path: Path) -> None:
    source = '''from acmm.integration_api import experiment_integration, subject_integration


class bad_integration:
    def compose_profile(self, context, profile):
        del context, profile
        return {"not": "a profile"}


def build_integration(context):
    del context
    return experiment_integration(
        subjects={"hermes": subject_integration(profile=bad_integration())}
    )
'''
    package_root = tmp_path / "source"
    _write_integration_package(package_root, integration_source=source)
    package = acquire_experiment_package(
        package_root,
        tmp_path / "runtime-root",
        contract_schema_path=CONTRACT_SCHEMA,
    )
    baseline = load_hermes_profile(ROOT)

    with pytest.raises(experiment_integration_error, match="preserve the subject profile type"):
        compose_staged_experiment_profile_integrations(
            package,
            "hermes",
            baseline.llm_baseline_id,
            baseline,
        )


def test_manager_integration_must_preserve_deployment_root(tmp_path: Path) -> None:
    source = '''from pathlib import Path

from acmm.integration_api import experiment_integration, subject_integration


class bad_manager:
    def __init__(self, manager):
        self.profile = manager.profile
        self.deployment_root = Path("/tmp/not-the-subject-runtime")
        self.docker = manager.docker

    def apply(self, **kwargs):
        return {}

    def status(self):
        return {}

    def doctor(self):
        return None

    def up(self, **kwargs):
        return None

    def down(self):
        return None


class bad_integration:
    def compose_manager(self, context, manager):
        del context
        return bad_manager(manager)


def build_integration(context):
    del context
    return experiment_integration(
        subjects={"hermes": subject_integration(manager=bad_integration())}
    )
'''
    _, package, runtime_root = _acquire(tmp_path, integration_source=source)

    with pytest.raises(experiment_integration_error, match="preserve the subject deployment root"):
        build_package_subject_manager(
            ROOT,
            runtime_root,
            "hermes",
            package,
        )


def test_shared_resource_lifecycle_uses_package_scoped_state(tmp_path: Path) -> None:
    _, package, runtime_root = _acquire(
        tmp_path,
        integration_source=RESOURCE_INTEGRATION_SOURCE,
    )

    progress = []
    results = apply_staged_integration_resources(
        package,
        runtime_root,
        progress=lambda integration_id, message: progress.append((integration_id, message)),
    )
    leases = acquire_staged_integration_resources(
        package,
        runtime_root,
        start_if_needed=True,
    )
    reports = doctor_staged_integration_resources(package, runtime_root)

    assert len(results) == 1
    assert results[0].integration_id == "profile-test"
    assert results[0].value["running"] is False
    state_root = Path(results[0].value["state_root"])
    assert state_root.is_relative_to(runtime_root.resolve())
    assert (state_root / "applied.txt").read_text(encoding="utf-8") == "ready"
    assert leases[0].initially_running is False
    assert leases[0].started_by_acmm is True
    assert reports[0][0] == "profile-test"
    assert reports[0][1].is_ready is True
    assert progress == [("profile-test", "reconciled")]

    errors = restore_staged_integration_resources(package, runtime_root, leases)
    assert errors == ()
    assert not (state_root / "running.txt").exists()

    down_results = down_staged_integration_resources(package, runtime_root)
    assert len(down_results) == 1
    assert down_results[0].integration_id == "profile-test"
    assert down_results[0].initially_running is False
    assert down_results[0].running_after is False
    assert down_results[0].value["running"] is False
    assert down_results[0].error is None


def test_superseded_revision_keeps_replacement_owned_external_resource(
    tmp_path: Path,
) -> None:
    source_code = RESOURCE_INTEGRATION_SOURCE.replace(
        "    def remove(self, context):\n",
        "    def remove(self, context):\n"
        "        (context.runtime_root / 'external-remove-called.txt').write_text(\n"
        "            'removed', encoding='utf-8'\n"
        "        )\n",
    )
    source, old, runtime_root = _acquire(
        tmp_path,
        integration_source=source_code,
    )
    results = apply_staged_integration_resources(old, runtime_root)
    old_state_root = Path(results[0].value["state_root"])
    assert old_state_root.is_dir()

    integration_file = source / "integrations" / "profile-test" / "__init__.py"
    integration_file.write_text(
        integration_file.read_text(encoding="utf-8") + "\n# replacement revision\n",
        encoding="utf-8",
    )
    replacement = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )
    assert replacement.package_sha256 != old.package_sha256

    remove_superseded_staged_integration_resources(
        old,
        replacement,
        runtime_root,
    )

    assert not old_state_root.exists()
    assert not (runtime_root / "external-remove-called.txt").exists()


def test_superseded_integration_removes_old_resource_when_replacement_has_none(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    runtime_root = tmp_path / "runtime-root"
    _write_integration_package(
        source,
        integration_source=RESOURCE_REMOVE_MARKER_SOURCE,
    )
    old = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    (source / "integrations" / "profile-test" / "__init__.py").write_text(
        INTEGRATION_SOURCE,
        encoding="utf-8",
    )
    replacement = acquire_experiment_package(
        source,
        runtime_root,
        contract_schema_path=CONTRACT_SCHEMA,
    )

    remove_superseded_staged_integration_resources(
        old,
        replacement,
        runtime_root,
    )

    assert (runtime_root / "old-resource-removed.txt").read_text(
        encoding="utf-8"
    ) == "removed"

def test_runtime_admission_broker_cleans_up_partial_admission_and_hides_handles(
    tmp_path: Path,
) -> None:
    from acmm.experiment_package import compose_staged_subject_admission_controller
    from acmm.runtime.interfaces import runtime_subject_admission_request

    _source, package, _runtime_root = _acquire(
        tmp_path,
        integration_source=ADMISSION_INTEGRATION_SOURCE,
    )
    loaded = load_staged_experiment_integration(package, "profile-test")
    admission = loaded.integration.subjects["hermes"].admission
    assert admission is not None

    controller = compose_staged_subject_admission_controller(
        package,
        {"hermes_gpt_oss120b": ("hermes", "gpt_oss_120b_cloud_v1")},
        lambda _subject_id: object(),
        loaded_integrations=(loaded,),
    )
    assert controller is not None

    secret_request = runtime_subject_admission_request(
        integration_id="profile-test",
        payload={"secret": "do-not-print"},
    )
    assert "do-not-print" not in repr(secret_request)

    with pytest.raises(experiment_integration_error, match="unknown integration"):
        controller.admit(
            "hermes_gpt_oss120b",
            (
                secret_request,
                runtime_subject_admission_request(
                    integration_id="missing-integration",
                    payload={},
                ),
            ),
        )

    assert admission.events == [
        ("admit", "hermes", {"secret": "do-not-print"}),
        ("revoke", "hermes", {"secret_handle": "do-not-print"}),
    ]

    result = controller.admit("hermes_gpt_oss120b", (secret_request,))
    assert "do-not-print" not in repr(result)
    assert result.provenance == {"integrations": ("profile-test",)}
    observations = controller.revoke("hermes_gpt_oss120b", result)
    assert observations == {
        "integrations": {"profile-test": {"revoked": True}}
    }

