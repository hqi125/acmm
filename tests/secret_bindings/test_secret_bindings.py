from pathlib import Path

import pytest

from acmm.secret_bindings import (
    bind_secret_file,
    list_secret_bindings,
    resolve_secret,
    secret_binding_error,
    secret_binding_key,
)


def test_file_binding_resolves_without_storing_value(tmp_path: Path, monkeypatch) -> None:
    registry = tmp_path / "bindings.yaml"
    source = tmp_path / "token"
    source.write_text("secret-value\n", encoding="utf-8")
    source.chmod(0o600)
    monkeypatch.setenv("ACMM_SECRET_BINDINGS_FILE", str(registry))
    key = secret_binding_key("experiment", "integration", "access_token")

    bind_secret_file(key, source)

    assert resolve_secret(key) == "secret-value"
    assert "secret-value" not in registry.read_text(encoding="utf-8")
    assert list_secret_bindings()[0].source_path == source.resolve()


def test_file_binding_rejects_group_or_other_access(tmp_path: Path, monkeypatch) -> None:
    if __import__("os").name != "posix":
        pytest.skip("POSIX permission test")
    source = tmp_path / "token"
    source.write_text("secret-value\n", encoding="utf-8")
    source.chmod(0o644)
    monkeypatch.setenv("ACMM_SECRET_BINDINGS_FILE", str(tmp_path / "bindings.yaml"))

    with pytest.raises(secret_binding_error, match="chmod 600"):
        bind_secret_file(
            secret_binding_key("experiment", "integration", "access_token"),
            source,
        )
