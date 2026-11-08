from pathlib import Path

import pytest

from acmm.cli import _build_parser


def _secure_secret(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value + "\n", encoding="utf-8")
    path.chmod(0o600)


def _configure_store(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    registry = tmp_path / "secret-bindings.yaml"
    directory = tmp_path / "secrets"
    monkeypatch.setenv("ACMM_SECRET_BINDINGS_FILE", str(registry))
    monkeypatch.setenv("ACMM_SECRET_DIRECTORY", str(directory))
    return registry, directory


def test_secret_cli_bind_requires_explicit_store_init(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _, directory = _configure_store(tmp_path, monkeypatch)
    source = directory / "ollama_api_key"
    _secure_secret(source, "ollama-value")
    parser = _build_parser()

    args = parser.parse_args(
        [
            "secret",
            "bind",
            "core",
            "ollama_api_key",
            "--file",
            str(source),
        ]
    )
    assert args.handler(args) == 2
    assert "acmm secret init" in capsys.readouterr().err


def test_secret_cli_initializes_and_supports_all_binding_scopes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _, directory = _configure_store(tmp_path, monkeypatch)
    parser = _build_parser()

    args = parser.parse_args(["secret", "init"])
    assert args.handler(args) == 0

    core_secret = directory / "ollama_api_key"
    experiment_secret = directory / "acmm-experiment-2-mcp-controller-pat"
    integration_secret = directory / "acmm-experiment-2-mcp-subject-pat"
    _secure_secret(core_secret, "ollama-value")
    _secure_secret(experiment_secret, "controller-value")
    _secure_secret(integration_secret, "subject-value")

    commands = (
        [
            "secret",
            "bind",
            "core",
            "ollama_api_key",
            "--file",
            str(core_secret),
        ],
        [
            "secret",
            "bind",
            "experiment",
            "github_mcp_project_workspace_v1",
            "controller_access_token",
            "--file",
            str(experiment_secret),
        ],
        [
            "secret",
            "bind",
            "integration",
            "github_mcp_project_workspace_v1",
            "github_mcp",
            "access_token",
            "--file",
            str(integration_secret),
        ],
    )
    for argv in commands:
        args = parser.parse_args(argv)
        assert args.handler(args) == 0

    capsys.readouterr()
    args = parser.parse_args(["secret", "validate"])
    assert args.handler(args) == 0
    args = parser.parse_args(["secret", "list"])
    assert args.handler(args) == 0
    output = capsys.readouterr().out

    assert "core:ollama_api_key" in output
    assert (
        "experiment:github_mcp_project_workspace_v1:controller_access_token"
        in output
    )
    assert (
        "integration:github_mcp_project_workspace_v1:github_mcp:access_token"
        in output
    )
    assert "ollama-value" not in output
    assert "controller-value" not in output
    assert "subject-value" not in output


def test_secret_cli_validate_rejects_stale_binding_after_source_moves(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _, directory = _configure_store(tmp_path, monkeypatch)
    parser = _build_parser()

    args = parser.parse_args(["secret", "init"])
    assert args.handler(args) == 0

    source = directory / "github_mcp_access_token"
    moved = directory / "qualification" / "github_mcp_access_token"
    _secure_secret(source, "qualification-value")

    args = parser.parse_args(
        [
            "secret",
            "bind",
            "integration",
            "github_mcp_integration_qualification_v1",
            "github_mcp",
            "access_token",
            "--file",
            str(source),
        ]
    )
    assert args.handler(args) == 0

    moved.parent.mkdir(parents=True)
    source.rename(moved)
    capsys.readouterr()

    args = parser.parse_args(["secret", "validate"])
    assert args.handler(args) == 2
    error = capsys.readouterr().err
    assert (
        "integration:github_mcp_integration_qualification_v1:github_mcp:access_token"
        in error
    )
    assert str(source.resolve()) in error
    assert "Secret source is not a file" in error


def test_secret_cli_validate_reports_usable_source_count(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _, directory = _configure_store(tmp_path, monkeypatch)
    parser = _build_parser()

    args = parser.parse_args(["secret", "init"])
    assert args.handler(args) == 0
    source = directory / "ollama_api_key"
    _secure_secret(source, "ollama-value")

    args = parser.parse_args(
        [
            "secret",
            "bind",
            "core",
            "ollama_api_key",
            "--file",
            str(source),
        ]
    )
    assert args.handler(args) == 0
    capsys.readouterr()

    args = parser.parse_args(["secret", "validate"])
    assert args.handler(args) == 0
    output = capsys.readouterr().out
    assert "schema:   0.2" in output
    assert "sources:  1/1 usable" in output


def test_secret_cli_bind_requires_replace_for_different_source(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    registry, directory = _configure_store(tmp_path, monkeypatch)
    parser = _build_parser()
    args = parser.parse_args(["secret", "init"])
    assert args.handler(args) == 0
    first = directory / "ollama_api_key"
    second = directory / "ollama_api_key_replacement"
    _secure_secret(first, "first")
    _secure_secret(second, "second")

    base = ["secret", "bind", "core", "ollama_api_key", "--file"]
    args = parser.parse_args(base + [str(first)])
    assert args.handler(args) == 0

    args = parser.parse_args(base + [str(first)])
    assert args.handler(args) == 0

    capsys.readouterr()
    args = parser.parse_args(base + [str(second)])
    assert args.handler(args) == 2
    assert "--replace" in capsys.readouterr().err
    assert str(first.resolve()) in registry.read_text(encoding="utf-8")

    args = parser.parse_args(base + [str(second), "--replace"])
    assert args.handler(args) == 0
    document = registry.read_text(encoding="utf-8")
    assert f"path: {second.resolve()}" in document
    assert f"path: {first.resolve()}\n" not in document


def test_secret_cli_unbind_is_exact_and_reports_same_name_other_scopes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _, directory = _configure_store(tmp_path, monkeypatch)
    parser = _build_parser()
    args = parser.parse_args(["secret", "init"])
    assert args.handler(args) == 0
    source = directory / "subject-token"
    _secure_secret(source, "subject-value")

    args = parser.parse_args(
        [
            "secret",
            "bind",
            "integration",
            "experiment_v1",
            "github_mcp",
            "access_token",
            "--file",
            str(source),
        ]
    )
    assert args.handler(args) == 0

    capsys.readouterr()
    args = parser.parse_args(
        ["secret", "unbind", "experiment", "experiment_v1", "access_token"]
    )
    assert args.handler(args) == 2
    error = capsys.readouterr().err
    assert "experiment:experiment_v1:access_token" in error
    assert "integration:experiment_v1:github_mcp:access_token" in error

    args = parser.parse_args(
        [
            "secret",
            "unbind",
            "integration",
            "experiment_v1",
            "github_mcp",
            "access_token",
        ]
    )
    assert args.handler(args) == 0
    assert source.read_text(encoding="utf-8") == "subject-value\n"


def test_secret_cli_upgrade_is_explicit(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    registry, directory = _configure_store(tmp_path, monkeypatch)
    directory.mkdir(parents=True)
    token = directory / "github_mcp_access_token"
    _secure_secret(token, "qualification-value")
    registry.write_text(
        "schema_version: '0.1'\n"
        "bindings:\n"
        "- experiment_id: github_mcp_integration_qualification_v1\n"
        "  integration_id: github_mcp\n"
        "  secret_id: access_token\n"
        "  source:\n"
        "    kind: file\n"
        f"    path: {token}\n",
        encoding="utf-8",
    )
    registry.chmod(0o600)
    parser = _build_parser()

    args = parser.parse_args(["secret", "init"])
    assert args.handler(args) == 0
    assert "schema_version: '0.1'" in registry.read_text(encoding="utf-8")

    args = parser.parse_args(["secret", "validate"])
    assert args.handler(args) == 2
    assert "acmm secret upgrade" in capsys.readouterr().err

    args = parser.parse_args(["secret", "upgrade"])
    assert args.handler(args) == 0
    assert "changed:  yes" in capsys.readouterr().out
    assert "schema_version: '0.2'" in registry.read_text(encoding="utf-8")


def test_secret_cli_uses_scope_subcommands_instead_of_legacy_bind_verbs() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        [
            "secret",
            "bind",
            "integration",
            "qualification_v1",
            "github_mcp",
            "access_token",
            "--file",
            "/tmp/token",
        ]
    )

    assert args.secret_scope == "integration"
    assert args.experiment_id == "qualification_v1"
    assert args.integration_id == "github_mcp"
    assert args.secret_id == "access_token"

    with pytest.raises(SystemExit):
        parser.parse_args(
            ["secret", "bind-core", "ollama_api_key", "--file", "/tmp/token"]
        )
