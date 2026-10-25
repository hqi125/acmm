from dataclasses import dataclass
from pathlib import Path

from acmm_infra.docker import docker_client
from acmm_infra.process import command_result


@dataclass(frozen=True)
class test_deployment_attributes:
    service_name: str = "agent-service"
    container_name: str = "acmm-agent"
    deployment_directory: Path = Path("agent-runtime")


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


def test_compose_uses_structural_deployment_attributes(
    tmp_path: Path,
) -> None:
    attributes = test_deployment_attributes()
    runner = recording_runner()
    client = docker_client(
        repo_root=tmp_path / "repo",
        runtime_root=tmp_path,
        attributes=attributes,
        runner=runner,
    )

    client.compose_config()

    call = runner.calls[-1]
    deployment_root = tmp_path / "agent-runtime"

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



def test_container_logs_requests_timestamped_bounded_merged_output(
    tmp_path: Path,
) -> None:
    attributes = test_deployment_attributes()
    runner = recording_runner()
    client = docker_client(
        repo_root=tmp_path / "repo",
        runtime_root=tmp_path,
        attributes=attributes,
        runner=runner,
    )

    result = client.container_logs(
        since="2026-10-25T15:00:00.000000Z",
    )

    assert result.succeeded
    call = runner.calls[-1]
    assert call["args"] == (
        "docker",
        "logs",
        "--timestamps",
        "--since",
        "2026-10-25T15:00:00.000000Z",
        "acmm-agent",
    )
    assert call["timeout_seconds"] == 30.0
    assert call["live_output"] is False
    assert call["line_handler"] is not None

def test_write_container_files_updates_without_replacing_tree(
    tmp_path: Path,
) -> None:
    attributes = test_deployment_attributes()
    runner = recording_runner()
    client = docker_client(
        repo_root=tmp_path / "repo",
        runtime_root=tmp_path,
        attributes=attributes,
        runner=runner,
    )

    client.write_container_files(
        "/a0/usr/agents/acmm",
        {"plugins/_tool_access/config.json": b"{}"},
    )

    call = runner.calls[-1]
    args = call["args"]
    assert args[:4] == ("docker", "exec", "-i", "acmm-agent")
    script = args[6]
    assert "shutil.rmtree" not in script
    assert args[-2] == "/a0/usr/agents/acmm"

def test_provision_uses_one_named_compose_container(
    tmp_path: Path,
) -> None:
    attributes = test_deployment_attributes()
    runner = recording_runner()
    client = docker_client(
        repo_root=tmp_path / "repo",
        runtime_root=tmp_path,
        attributes=attributes,
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
        str(tmp_path / "agent-runtime" / ".env"),
        "-f",
        str(tmp_path / "agent-runtime" / "compose.yaml"),
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
    attributes = test_deployment_attributes()
    runner = timeout_runner()
    client = docker_client(
        repo_root=tmp_path / "repo",
        runtime_root=tmp_path,
        attributes=attributes,
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
    attributes = test_deployment_attributes()
    runner = recording_runner()
    client = docker_client(
        repo_root=tmp_path / "repo",
        runtime_root=tmp_path,
        attributes=attributes,
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
    attributes = test_deployment_attributes()
    runner = recording_runner()
    client = docker_client(
        repo_root=tmp_path / "repo",
        runtime_root=tmp_path,
        attributes=attributes,
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
                "container bootstrap",
                "provision step 1",
                "configuration updated",
                "provisioning complete",
                "container shutdown",
            ):
                line_handler(line)

        return result


def test_provision_normal_mode_uses_caller_line_handler(
    tmp_path: Path,
) -> None:
    attributes = test_deployment_attributes()
    runner = filtering_runner()
    client = docker_client(
        repo_root=tmp_path / "repo",
        runtime_root=tmp_path,
        attributes=attributes,
        runner=runner,
    )
    messages: list[str] = []

    result = client.provision(
        "echo provisioning",
        timeout_seconds=42.0,
        output_mode="normal",
        line_handler=messages.append,
    )

    assert result.succeeded
    assert messages == [
        "container bootstrap",
        "provision step 1",
        "configuration updated",
        "provisioning complete",
        "container shutdown",
    ]



def test_provision_uses_caller_failure_transform(
    tmp_path: Path,
) -> None:
    attributes = test_deployment_attributes()
    runner = timeout_runner()
    client = docker_client(
        repo_root=tmp_path / "repo",
        runtime_root=tmp_path,
        attributes=attributes,
        runner=runner,
    )

    def transform(result: command_result) -> command_result:
        return command_result(
            args=result.args,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr="transformed failure",
            timed_out=result.timed_out,
        )

    result = client.provision(
        "sleep forever",
        timeout_seconds=5.0,
        failure_transform=transform,
    )

    assert not result.succeeded
    assert result.stderr == "transformed failure"
