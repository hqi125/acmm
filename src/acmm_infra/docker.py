from __future__ import annotations

from pathlib import Path
from typing import Callable, Literal, Mapping, Protocol, Sequence
import base64
import json
import uuid

from acmm_infra.process import command_result, command_runner


class docker_deployment_attributes(Protocol):
    service_name: str
    container_name: str
    deployment_directory: Path


class docker_error(RuntimeError):
    """Raised when Docker or Docker Compose commands fail."""


class docker_client:
    def __init__(
        self,
        repo_root: str | Path,
        runtime_root: str | Path,
        attributes: docker_deployment_attributes,
        runner: command_runner | None = None,
    ) -> None:
        self._repo_root = Path(repo_root).resolve()
        self._runtime_root = Path(runtime_root).expanduser().resolve()
        self._attributes = attributes
        self._runner = runner or command_runner()
        self._deployment_root = (
            self._runtime_root / attributes.deployment_directory
        ).resolve()
        self._compose_file = self._deployment_root / "compose.yaml"
        self._env_file = self._deployment_root / ".env"

    def docker_version(self) -> command_result:
        return self._runner.run(["docker", "--version"])

    def compose_version(self) -> command_result:
        return self._runner.run(["docker", "compose", "version"])

    def compose_config(self) -> command_result:
        return self._compose(["config", "--quiet"])

    def up(self) -> command_result:
        return self._compose(["up", "-d", self._attributes.service_name])

    def down(self) -> command_result:
        return self._compose(["down"])

    def container_info(self) -> tuple[bool, str | None]:
        result = self._runner.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{.State.Running}}\t{{.Config.Image}}",
                self._attributes.container_name,
            ],
            timeout_seconds=15,
        )

        if not result.succeeded:
            return False, None

        fields = result.stdout.strip().split("\t", maxsplit=1)
        running = bool(fields) and fields[0].lower() == "true"
        image = fields[1] if len(fields) == 2 and fields[1] else None
        return running, image

    def container_running(self) -> bool:
        running, _ = self.container_info()
        return running

    def container_image(self) -> str | None:
        _, image = self.container_info()
        return image

    def container_logs(
        self,
        *,
        since: str,
        timeout_seconds: float = 30.0,
    ) -> command_result:
        return self._runner.run(
            [
                "docker",
                "logs",
                "--timestamps",
                "--since",
                since,
                self._attributes.container_name,
            ],
            timeout_seconds=timeout_seconds,
            line_handler=lambda line: None,
        )

    def exec(
        self,
        args: Sequence[str],
    ) -> command_result:
        return self._runner.run(
            [
                "docker",
                "exec",
                "-i",
                self._attributes.container_name,
                *args,
            ],
            timeout_seconds=30,
        )

    def exec_script(
        self,
        script: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> command_result:
        return self._runner.run(
            [
                "docker",
                "exec",
                "-i",
                self._attributes.container_name,
                "sh",
                "-lc",
                script,
            ],
            timeout_seconds=timeout_seconds,
        )

    def replace_container_tree(
        self,
        root: str,
        files: Mapping[str, bytes],
        *,
        timeout_seconds: float = 30.0,
    ) -> command_result:
        payload = {
            path: base64.b64encode(content).decode("ascii")
            for path, content in files.items()
        }
        script = (
            "import base64,json,pathlib,shutil,sys; "
            "root=pathlib.Path(sys.argv[1]); "
            "data=json.loads(sys.argv[2]); "
            "shutil.rmtree(root, ignore_errors=True); "
            "[(root.joinpath(path).parent.mkdir(parents=True, exist_ok=True), "
            "root.joinpath(path).write_bytes(base64.b64decode(value))) "
            "for path,value in data.items()]"
        )
        return self._runner.run(
            [
                "docker",
                "exec",
                "-i",
                self._attributes.container_name,
                "python3",
                "-c",
                script,
                root,
                json.dumps(payload, separators=(",", ":")),
            ],
            timeout_seconds=timeout_seconds,
        )

    def write_container_files(
        self,
        root: str,
        files: Mapping[str, bytes],
        *,
        timeout_seconds: float = 30.0,
    ) -> command_result:
        payload = {
            path: base64.b64encode(content).decode("ascii")
            for path, content in files.items()
        }
        script = (
            "import base64,json,pathlib,sys; "
            "root=pathlib.Path(sys.argv[1]); "
            "data=json.loads(sys.argv[2]); "
            "[(root.joinpath(path).parent.mkdir(parents=True, exist_ok=True), "
            "root.joinpath(path).write_bytes(base64.b64decode(value))) "
            "for path,value in data.items()]"
        )
        return self._runner.run(
            [
                "docker",
                "exec",
                "-i",
                self._attributes.container_name,
                "python3",
                "-c",
                script,
                root,
                json.dumps(payload, separators=(",", ":")),
            ],
            timeout_seconds=timeout_seconds,
        )

    def provision(
        self,
        script: str,
        timeout_seconds: float,
        *,
        output_mode: Literal["quiet", "normal", "verbose"] = "normal",
        line_handler: Callable[[str], None] | None = None,
        failure_transform: Callable[[command_result], command_result] | None = None,
    ) -> command_result:
        container_name = (
            f"{self._attributes.container_name}-provision-"
            f"{uuid.uuid4().hex[:8]}"
        )

        if output_mode not in {"quiet", "normal", "verbose"}:
            raise ValueError(
                f"Unknown provisioning output mode: '{output_mode}'."
            )

        active_line_handler = None
        live_output = False

        if output_mode == "verbose":
            live_output = True
        elif output_mode == "normal":
            active_line_handler = line_handler

        try:
            result = self._compose(
                [
                    "run",
                    "--rm",
                    "--no-deps",
                    "--name",
                    container_name,
                    self._attributes.service_name,
                    "sh",
                    "-lc",
                    script,
                ],
                timeout_seconds=timeout_seconds,
                live_output=live_output,
                line_handler=active_line_handler,
            )
        except KeyboardInterrupt:
            self._force_remove(container_name)
            raise

        if not result.succeeded:
            self._force_remove(container_name)

            if output_mode != "verbose" and failure_transform is not None:
                result = failure_transform(result)

        return result


    def _force_remove(
        self,
        container_name: str,
    ) -> None:
        self._runner.run(
            [
                "docker",
                "rm",
                "-f",
                container_name,
            ],
            timeout_seconds=15,
        )

    def _compose(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: float | None = None,
        live_output: bool = False,
        line_handler: Callable[[str], None] | None = None,
    ) -> command_result:
        return self._runner.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(self._env_file),
                "-f",
                str(self._compose_file),
                *args,
            ],
            cwd=self._deployment_root,
            timeout_seconds=timeout_seconds,
            live_output=live_output,
            line_handler=line_handler,
        )


def require_success(
    result: command_result,
    description: str,
) -> command_result:
    if result.succeeded:
        return result

    detail = result.stderr.strip() or result.stdout.strip()

    if not detail:
        detail = f"exit status {result.returncode}"

    raise docker_error(f"{description} failed: {detail}")
