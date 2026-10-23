from __future__ import annotations

import argparse
from pathlib import Path
import sys

from acmm_infra.agent_zero_cli import agent_zero_cli
from acmm_infra.common import infrastructure_error
from acmm_infra.docker import docker_error
from acmm_infra.hermes_cli import hermes_cli
from acmm_infra.open_webui_cli import open_webui_cli
from acmm_infra.profiles.common import default_repo_root, default_runtime_root, profile_error


AGENT_CLIS = (
    hermes_cli(),
    open_webui_cli(),
    agent_zero_cli(),
)


def _add_root_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo-root",
        default=str(default_repo_root()),
        help="ACMM repository root.",
    )
    parser.add_argument(
        "--runtime-root",
        default=str(default_runtime_root()),
        help="Mutable ACMM runtime root. Defaults to ~/acmm_runtime.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="acmm-infra",
        description="ACMM infrastructure deployment and inspection tooling.",
    )
    _add_root_arguments(parser)

    subparsers = parser.add_subparsers(dest="command", required=True)
    for agent in AGENT_CLIS:
        agent.register(subparsers)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        return args.handler(args)
    except KeyboardInterrupt:
        print(
            "ERROR: operation interrupted; temporary provisioning state was "
            "cleaned up.",
            file=sys.stderr,
        )
        return 130
    except (profile_error, infrastructure_error, docker_error, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
