from __future__ import annotations


import pytest

from acmm.cli import _build_parser as build_acmm_parser
from acmm_infra.cli import _build_parser as build_infra_parser


EXPERIMENT = "controlled_tool_use_multiscenario_v1"


def test_acmm_exposes_environment_command_family() -> None:
    parser = build_acmm_parser()

    args = parser.parse_args(
        [
            "env",
            "apply",
            "--experiment",
            EXPERIMENT,
            "--subject",
            "hermes_nemotron30b",
        ]
    )
    assert args.command == "env"
    assert args.env_command == "apply"
    assert args.experiment_id == EXPERIMENT
    assert args.subjects == ["hermes_nemotron30b"]
    assert args.timeout_seconds == 120.0

    args = parser.parse_args(["env", "status", "--json"])
    assert args.command == "env"
    assert args.env_command == "status"
    assert args.json is True

    args = parser.parse_args(
        [
            "env",
            "doctor",
            "--subject",
            "agent_zero_nemotron30b",
            "--start-if-needed",
        ]
    )
    assert args.command == "env"
    assert args.env_command == "doctor"
    assert args.subjects == ["agent_zero_nemotron30b"]
    assert args.start_if_needed is True
    assert args.startup_timeout_seconds == 120.0

    args = parser.parse_args(["env", "stop", "--subject", "hermes_nemotron30b"])
    assert args.command == "env"
    assert args.env_command == "stop"
    assert args.subjects == ["hermes_nemotron30b"]


def test_acmm_infra_root_accepts_only_subject_backends() -> None:
    parser = build_infra_parser()

    for command in ("apply", "status", "doctor"):
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args([command])
        assert exc_info.value.code == 2

    for backend in ("hermes", "open-webui", "agent-zero"):
        args = parser.parse_args([backend, "status"])
        assert args.command == backend
