from pathlib import Path
import os

import pytest

from acmm.secret_bindings import (
    CURRENT_SECRET_BINDINGS_SCHEMA_VERSION,
    bind_secret_file,
    core_secret_binding_key,
    default_secret_directory_path,
    experiment_secret_binding_key,
    initialize_secret_store,
    list_secret_bindings,
    resolve_secret,
    secret_binding_error,
    secret_binding_key,
    secret_binding_version_error,
    unbind_secret,
    upgrade_secret_bindings_registry,
    validate_secret_bindings_registry,
)


def _configure_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    registry = tmp_path / "secret-bindings.yaml"
    directory = tmp_path / "secrets"
    monkeypatch.setenv("ACMM_SECRET_BINDINGS_FILE", str(registry))
    monkeypatch.setenv("ACMM_SECRET_DIRECTORY", str(directory))
    return registry, directory


def _secure_secret(path: Path, value: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def test_secret_store_init_creates_current_registry_and_secure_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, directory = _configure_store(tmp_path, monkeypatch)

    state = initialize_secret_store()

    assert state.registry_created is True
    assert state.registry_schema_version == CURRENT_SECRET_BINDINGS_SCHEMA_VERSION
    assert state.registry_is_current is True
    assert state.bindings_path == registry.resolve()
    assert state.secret_directory == directory.resolve()
    assert default_secret_directory_path() == directory.resolve()
    assert "schema_version: '0.2'" in registry.read_text(encoding="utf-8")
    if os.name == "posix":
        assert registry.stat().st_mode & 0o777 == 0o600
        assert directory.stat().st_mode & 0o777 == 0o700


def test_low_level_binding_api_preserves_missing_registry_compatibility(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _ = _configure_store(tmp_path, monkeypatch)
    source = _secure_secret(tmp_path / "token", "secret-value")
    key = secret_binding_key("experiment", "integration", "access_token")

    bind_secret_file(key, source)

    assert registry.is_file()
    assert validate_secret_bindings_registry() == CURRENT_SECRET_BINDINGS_SCHEMA_VERSION
    assert resolve_secret(key) == "secret-value"


def test_file_binding_resolves_without_storing_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, directory = _configure_store(tmp_path, monkeypatch)
    initialize_secret_store()
    source = _secure_secret(directory / "token", "secret-value")
    key = secret_binding_key("experiment", "integration", "access_token")

    bind_secret_file(key, source)

    assert resolve_secret(key) == "secret-value"
    assert "secret-value" not in registry.read_text(encoding="utf-8")
    assert list_secret_bindings()[0].source_path == source.resolve()


def test_file_binding_rejects_group_or_other_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX permission test")
    _configure_store(tmp_path, monkeypatch)
    initialize_secret_store()
    source = tmp_path / "token"
    source.write_text("secret-value\n", encoding="utf-8")
    source.chmod(0o644)

    with pytest.raises(secret_binding_error, match="chmod 600"):
        bind_secret_file(
            secret_binding_key("experiment", "integration", "access_token"),
            source,
        )


def test_core_and_experiment_bindings_share_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from acmm.secret_bindings import secret_binding_label

    registry, directory = _configure_store(tmp_path, monkeypatch)
    initialize_secret_store()
    core_source = _secure_secret(directory / "ollama-token", "ollama-secret")
    experiment_source = _secure_secret(
        directory / "controller-token",
        "controller-secret",
    )

    core_key = core_secret_binding_key("ollama_api_key")
    experiment_key = experiment_secret_binding_key(
        "github_mcp_project_workspace_v1",
        "controller_access_token",
    )
    bind_secret_file(core_key, core_source)
    bind_secret_file(experiment_key, experiment_source)

    assert resolve_secret(core_key) == "ollama-secret"
    assert resolve_secret(experiment_key) == "controller-secret"
    labels = [secret_binding_label(item.key) for item in list_secret_bindings()]
    assert labels == [
        "core:ollama_api_key",
        "experiment:github_mcp_project_workspace_v1:controller_access_token",
    ]
    document = registry.read_text(encoding="utf-8")
    assert "schema_version: '0.2'" in document
    assert "ollama-secret" not in document
    assert "controller-secret" not in document


def test_legacy_registry_requires_explicit_upgrade_and_preserves_qualification_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, directory = _configure_store(tmp_path, monkeypatch)
    directory.mkdir(parents=True)
    integration_source = _secure_secret(
        directory / "github_mcp_access_token",
        "integration-secret",
    )
    registry.write_text(
        "schema_version: '0.1'\n"
        "bindings:\n"
        "- experiment_id: github_mcp_integration_qualification_v1\n"
        "  integration_id: github_mcp\n"
        "  secret_id: access_token\n"
        "  source:\n"
        "    kind: file\n"
        f"    path: {integration_source}\n",
        encoding="utf-8",
    )
    registry.chmod(0o600)

    state = initialize_secret_store()
    assert state.registry_created is False
    assert state.registry_schema_version == "0.1"
    assert state.registry_is_current is False
    assert "scope:" not in registry.read_text(encoding="utf-8")

    legacy_key = secret_binding_key(
        "github_mcp_integration_qualification_v1",
        "github_mcp",
        "access_token",
    )
    with pytest.raises(secret_binding_version_error, match="acmm secret upgrade"):
        resolve_secret(legacy_key)

    result = upgrade_secret_bindings_registry()
    assert result.source_version == "0.1"
    assert result.target_version == "0.2"
    assert result.changed is True
    migrated = registry.read_text(encoding="utf-8")
    assert "schema_version: '0.2'" in migrated
    assert "scope: integration" in migrated
    assert "github_mcp_integration_qualification_v1" in migrated
    assert str(integration_source.resolve()) in migrated
    assert resolve_secret(legacy_key) == "integration-secret"


def test_registry_schema_validation_rejects_unrecognized_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _ = _configure_store(tmp_path, monkeypatch)
    registry.write_text(
        "schema_version: '0.2'\n"
        "bindings: []\n"
        "unexpected: true\n",
        encoding="utf-8",
    )
    registry.chmod(0o600)

    with pytest.raises(secret_binding_error, match="schema 0.2 violation"):
        validate_secret_bindings_registry()


def test_registry_requires_explicit_schema_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, _ = _configure_store(tmp_path, monkeypatch)
    registry.write_text("bindings: []\n", encoding="utf-8")
    registry.chmod(0o600)

    with pytest.raises(secret_binding_error, match="requires a non-empty string schema_version"):
        validate_secret_bindings_registry()



def test_exact_binding_key_is_unique_but_secret_id_may_repeat_across_scopes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, directory = _configure_store(tmp_path, monkeypatch)
    initialize_secret_store()
    core_source = _secure_secret(directory / "core-token", "core-secret")
    experiment_source = _secure_secret(directory / "experiment-token", "experiment-secret")
    integration_source = _secure_secret(directory / "integration-token", "integration-secret")

    bind_secret_file(core_secret_binding_key("access_token"), core_source)
    bind_secret_file(
        experiment_secret_binding_key("experiment_v1", "access_token"),
        experiment_source,
    )
    bind_secret_file(
        secret_binding_key("experiment_v1", "github_mcp", "access_token"),
        integration_source,
    )

    assert len(list_secret_bindings()) == 3
    document = registry.read_text(encoding="utf-8")
    assert document.count("secret_id: access_token") == 3


def test_bind_same_key_same_source_is_idempotent_and_replacement_is_explicit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, directory = _configure_store(tmp_path, monkeypatch)
    initialize_secret_store()
    first = _secure_secret(directory / "first-token", "first")
    second = _secure_secret(directory / "second-token", "second")
    key = core_secret_binding_key("ollama_api_key")

    bind_secret_file(key, first)
    bind_secret_file(key, first)
    assert len(list_secret_bindings()) == 1
    assert list_secret_bindings()[0].source_path == first.resolve()

    with pytest.raises(secret_binding_error, match="--replace"):
        bind_secret_file(key, second)

    bind_secret_file(key, second, replace=True)
    assert len(list_secret_bindings()) == 1
    assert list_secret_bindings()[0].source_path == second.resolve()


def test_registry_rejects_duplicate_exact_binding_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry, directory = _configure_store(tmp_path, monkeypatch)
    directory.mkdir(parents=True)
    source = _secure_secret(directory / "token", "secret-value")
    registry.write_text(
        "schema_version: '0.2'\n"
        "bindings:\n"
        "- scope: core\n"
        "  secret_id: ollama_api_key\n"
        "  source:\n"
        "    kind: file\n"
        f"    path: {source}\n"
        "- scope: core\n"
        "  secret_id: ollama_api_key\n"
        "  source:\n"
        "    kind: file\n"
        f"    path: {source}\n",
        encoding="utf-8",
    )
    registry.chmod(0o600)

    with pytest.raises(secret_binding_error, match="Duplicate ACMM secret binding key"):
        validate_secret_bindings_registry()

def test_validate_registry_checks_all_bound_secret_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, directory = _configure_store(tmp_path, monkeypatch)
    initialize_secret_store()

    missing = _secure_secret(directory / "missing-token", "missing-value")
    empty = _secure_secret(directory / "empty-token", "")
    insecure = _secure_secret(directory / "insecure-token", "insecure-value")

    bind_secret_file(core_secret_binding_key("missing_secret"), missing)
    bind_secret_file(core_secret_binding_key("empty_secret"), empty)
    bind_secret_file(core_secret_binding_key("insecure_secret"), insecure)

    missing.unlink()
    if os.name == "posix":
        insecure.chmod(0o644)

    with pytest.raises(secret_binding_error) as caught:
        validate_secret_bindings_registry()

    message = str(caught.value)
    assert "core:missing_secret" in message
    assert "Secret source is not a file" in message
    assert "core:empty_secret" in message
    assert "is empty" in message
    if os.name == "posix":
        assert "core:insecure_secret" in message
        assert "chmod 600" in message


def test_unbind_never_deletes_secret_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, directory = _configure_store(tmp_path, monkeypatch)
    initialize_secret_store()
    source = _secure_secret(directory / "token", "secret-value")
    key = core_secret_binding_key("ollama_api_key")
    bind_secret_file(key, source)

    assert unbind_secret(key) is True
    assert source.read_text(encoding="utf-8") == "secret-value\n"
