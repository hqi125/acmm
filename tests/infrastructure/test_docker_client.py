from pathlib import Path

from acmm_infra.docker import docker_client
from acmm_infra.process import command_result
from acmm_infra.profile import load_hermes_profile


ROOT = Path(__file__).resolve().parents[2]


class recording_runner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def run(
        self,
        args,
        *,
        env=None,
        cwd=None,
        timeout_seconds=None,
        live_output=False,
        line_handler=None,
    ) -> command_result:
        self.calls.append(
            {
                "args": tuple(args),
                "env": env,
                "cwd": cwd,
                "timeout_seconds": timeout_seconds,
                "live_output": live_output,
                "line_handler": line_handler,
            }
        )
        return command_result(
            args=tuple(args),
            returncode=0,
            stdout="",
            stderr="",
        )


def test_compose_uses_initialized_hermes_deployment(
    tmp_path: Path,
) -> None:
    profile = load_hermes_profile(ROOT)
    runner = recording_runner()
    client = docker_client(
        repo_root=ROOT,
        runtime_root=tmp_path,
        profile=profile,
        runner=runner,
    )

    client.compose_config()

    call = runner.calls[-1]
    deployment_root = tmp_path / "hermes"

    assert call["cwd"] == deployment_root
    assert call["args"] == (
        "docker",
        "compose",
        "--env-file",
        str(deployment_root / ".env"),
        "-f",
        str(deployment_root / "compose.yaml"),
        "config",
        "--quiet",
    )

def test_provision_uses_one_named_compose_container(
    tmp_path: Path,
) -> None:
    profile = load_hermes_profile(ROOT)
    runner = recording_runner()
    client = docker_client(
        repo_root=ROOT,
        runtime_root=tmp_path,
        profile=profile,
        runner=runner,
    )

    result = client.provision(
        "echo provisioning",
        timeout_seconds=42.0,
    )

    assert result.succeeded
    assert len(runner.calls) == 1

    call = runner.calls[0]
    args = call["args"]

    assert args[:6] == (
        "docker",
        "compose",
        "--env-file",
        str(tmp_path / "hermes" / ".env"),
        "-f",
        str(tmp_path / "hermes" / "compose.yaml"),
    )
    assert "run" in args
    assert "--rm" in args
    assert "--no-deps" in args
    assert "--name" in args
    assert args[-3:] == ("sh", "-lc", "echo provisioning")
    assert call["timeout_seconds"] == 42.0
    assert call["live_output"] is False
    assert call["line_handler"] is None


class timeout_runner(recording_runner):
    def run(
        self,
        args,
        *,
        env=None,
        cwd=None,
        timeout_seconds=None,
        live_output=False,
        line_handler=None,
    ) -> command_result:
        call = {
            "args": tuple(args),
            "env": env,
            "cwd": cwd,
            "timeout_seconds": timeout_seconds,
            "live_output": live_output,
            "line_handler": line_handler,
        }
        self.calls.append(call)

        if "compose" in args and "run" in args:
            return command_result(
                args=tuple(args),
                returncode=124,
                stdout="",
                stderr="Command timed out after 5 seconds.",
                timed_out=True,
            )

        return command_result(
            args=tuple(args),
            returncode=0,
            stdout="",
            stderr="",
        )


def test_provision_force_removes_timed_out_container(
    tmp_path: Path,
) -> None:
    profile = load_hermes_profile(ROOT)
    runner = timeout_runner()
    client = docker_client(
        repo_root=ROOT,
        runtime_root=tmp_path,
        profile=profile,
        runner=runner,
    )

    result = client.provision(
        "sleep forever",
        timeout_seconds=5.0,
    )

    assert result.timed_out
    assert len(runner.calls) == 2

    provision_args = runner.calls[0]["args"]
    name_index = provision_args.index("--name")
    provision_name = provision_args[name_index + 1]

    assert runner.calls[1]["args"] == (
        "docker",
        "rm",
        "-f",
        provision_name,
    )
    assert runner.calls[1]["timeout_seconds"] == 15

def test_provision_verbose_streams_raw_output(
    tmp_path: Path,
) -> None:
    profile = load_hermes_profile(ROOT)
    runner = recording_runner()
    client = docker_client(
        repo_root=ROOT,
        runtime_root=tmp_path,
        profile=profile,
        runner=runner,
    )

    result = client.provision(
        "echo provisioning",
        timeout_seconds=42.0,
        output_mode="verbose",
    )

    assert result.succeeded
    call = runner.calls[0]
    assert call["live_output"] is True
    assert call["line_handler"] is None


def test_provision_quiet_captures_without_streaming(
    tmp_path: Path,
) -> None:
    profile = load_hermes_profile(ROOT)
    runner = recording_runner()
    client = docker_client(
        repo_root=ROOT,
        runtime_root=tmp_path,
        profile=profile,
        runner=runner,
    )

    result = client.provision(
        "echo provisioning",
        timeout_seconds=42.0,
        output_mode="quiet",
    )

    assert result.succeeded
    call = runner.calls[0]
    assert call["live_output"] is False
    assert call["line_handler"] is None

class filtering_runner(recording_runner):
    def run(
        self,
        args,
        *,
        env=None,
        cwd=None,
        timeout_seconds=None,
        live_output=False,
        line_handler=None,
    ) -> command_result:
        result = super().run(
            args,
            env=env,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
            live_output=live_output,
            line_handler=line_handler,
        )

        if "compose" in args and "run" in args and line_handler is not None:
            for line in (
                "s6-rc: info: noisy bootstrap",
                "[Hermes 1/8] set model.provider",
                "✓ Set model.provider = custom",
                "[Hermes] Provisioning batch complete.",
                "s6-rc: info: noisy shutdown",
            ):
                line_handler(line)

        return result


def test_provision_normal_mode_filters_container_noise(
    tmp_path: Path,
) -> None:
    profile = load_hermes_profile(ROOT)
    runner = filtering_runner()
    client = docker_client(
        repo_root=ROOT,
        runtime_root=tmp_path,
        profile=profile,
        runner=runner,
    )
    messages: list[str] = []

    result = client.provision(
        "echo provisioning",
        timeout_seconds=42.0,
        output_mode="normal",
        output=messages.append,
    )

    assert result.succeeded
    assert messages == [
        "[Hermes 1/8] set model.provider",
        "✓ Set model.provider = custom",
        "[Hermes] Provisioning batch complete.",
    ]

