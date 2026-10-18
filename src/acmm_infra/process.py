from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import threading
from typing import Callable, Mapping, Sequence


@dataclass(frozen=True)
class command_result:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and not self.timed_out


class command_runner:
    def run(
        self,
        args: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        cwd: str | Path | None = None,
        timeout_seconds: float | None = None,
        live_output: bool = False,
        line_handler: Callable[[str], None] | None = None,
    ) -> command_result:
        normalized_args = tuple(str(arg) for arg in args)

        if live_output or line_handler is not None:
            return self._run_streaming(
                normalized_args,
                env=env,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
                live_output=live_output,
                line_handler=line_handler,
            )

        try:
            completed = subprocess.run(
                list(normalized_args),
                env=None if env is None else dict(env),
                cwd=None if cwd is None else str(cwd),
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            timeout_text = _timeout_text(timeout_seconds)
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""

            if stderr:
                stderr = stderr.rstrip() + "\n" + timeout_text
            else:
                stderr = timeout_text

            return command_result(
                args=normalized_args,
                returncode=124,
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
            )
        except OSError as exc:
            return command_result(
                args=normalized_args,
                returncode=127,
                stdout="",
                stderr=str(exc),
            )

        return command_result(
            args=normalized_args,
            returncode=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    def _run_streaming(
        self,
        normalized_args: tuple[str, ...],
        *,
        env: Mapping[str, str] | None,
        cwd: str | Path | None,
        timeout_seconds: float | None,
        live_output: bool,
        line_handler: Callable[[str], None] | None,
    ) -> command_result:
        try:
            process = subprocess.Popen(
                list(normalized_args),
                env=None if env is None else dict(env),
                cwd=None if cwd is None else str(cwd),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
            )
        except OSError as exc:
            return command_result(
                args=normalized_args,
                returncode=127,
                stdout="",
                stderr=str(exc),
            )

        captured: list[str] = []

        def drain_output() -> None:
            assert process.stdout is not None

            for line in process.stdout:
                captured.append(line)

                if live_output:
                    print(line, end="", flush=True)

                if line_handler is not None:
                    line_handler(line.rstrip("\r\n"))

        reader = threading.Thread(
            target=drain_output,
            name="acmm-command-output",
            daemon=True,
        )
        reader.start()

        timed_out = False

        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
        except KeyboardInterrupt:
            process.kill()
            process.wait()
            reader.join(timeout=5)
            raise

        reader.join(timeout=5)

        stdout = "".join(captured)
        stderr = _timeout_text(timeout_seconds) if timed_out else ""

        return command_result(
            args=normalized_args,
            returncode=124 if timed_out else process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
        )


def _timeout_text(timeout_seconds: float | None) -> str:
    if timeout_seconds is None:
        return "Command timed out."

    return f"Command timed out after {timeout_seconds:g} seconds."

