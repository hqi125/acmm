from acmm_infra.cli import _build_parser


def test_cli_exposes_infrastructure_commands() -> None:
    parser = _build_parser()

    args = parser.parse_args(["hermes", "init"])
    assert args.command == "hermes"
    assert args.hermes_command == "init"

    args = parser.parse_args(["hermes", "up"])
    assert args.hermes_command == "up"

    args = parser.parse_args(["hermes", "down"])
    assert args.hermes_command == "down"

    args = parser.parse_args([
        "hermes",
        "apply",
        "--timeout-seconds",
        "45",
    ])
    assert args.hermes_command == "apply"
    assert args.timeout_seconds == 45.0
    assert args.quiet is False
    assert args.verbose is False

    args = parser.parse_args(["hermes", "apply", "--quiet"])
    assert args.quiet is True
    assert args.verbose is False

    args = parser.parse_args(["hermes", "apply", "--verbose"])
    assert args.verbose is True
    assert args.quiet is False

    args = parser.parse_args([
        "hermes",
        "apply-tools",
        "--timeout-seconds",
        "30",
    ])
    assert args.hermes_command == "apply-tools"
    assert args.timeout_seconds == 30.0
    assert args.quiet is False
    assert args.verbose is False

    args = parser.parse_args(["hermes", "apply-tools", "--quiet"])
    assert args.quiet is True
    assert args.verbose is False

    args = parser.parse_args(["hermes", "doctor", "--json"])
    assert args.hermes_command == "doctor"
    assert args.json is True

    args = parser.parse_args(["hermes", "reset", "sessions", "--yes"])
    assert args.scope == "sessions"
    assert args.yes is True


def test_cli_exposes_hermes_data_adoption() -> None:
    parser = _build_parser()
    args = parser.parse_args([
        "hermes",
        "adopt-data",
        "/tmp/old-hermes-data",
        "--yes",
    ])

    assert args.hermes_command == "adopt-data"
    assert args.source == "/tmp/old-hermes-data"
    assert args.yes is True
