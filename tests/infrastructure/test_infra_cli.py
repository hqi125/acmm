from acmm_infra.commands.agent import STANDARD_AGENT_COMMANDS
from acmm_infra.cli import AGENT_CLIS, _build_parser


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


def test_cli_exposes_open_webui_lifecycle() -> None:
    parser = _build_parser()

    args = parser.parse_args(["open-webui", "init"])
    assert args.command == "open-webui"
    assert args.open_webui_command == "init"

    args = parser.parse_args(["open-webui", "up"])
    assert args.open_webui_command == "up"
    assert args.timeout_seconds == 60.0

    args = parser.parse_args(["open-webui", "apply"])
    assert args.open_webui_command == "apply"
    assert args.timeout_seconds == 120.0

    args = parser.parse_args(["open-webui", "apply-tools"])
    assert args.open_webui_command == "apply-tools"
    assert args.timeout_seconds == 60.0

    for command in ["down", "status", "doctor"]:
        args = parser.parse_args(["open-webui", command])
        assert args.open_webui_command == command

    args = parser.parse_args(["open-webui", "reset", "configuration", "--yes"])
    assert args.scope == "configuration"
    assert args.yes is True


def test_all_agent_clis_expose_standard_lifecycle() -> None:
    expected = {definition.name for definition in STANDARD_AGENT_COMMANDS}

    for agent in AGENT_CLIS:
        actual = {definition.name for definition in agent.command_definitions()}
        assert expected <= actual



def test_cli_exposes_agent_zero_lifecycle() -> None:
    parser = _build_parser()

    args = parser.parse_args(["agent-zero", "init"])
    assert args.command == "agent-zero"
    assert args.agent_zero_command == "init"

    args = parser.parse_args(["agent-zero", "up"])
    assert args.agent_zero_command == "up"
    assert args.timeout_seconds == 120.0

    args = parser.parse_args(["agent-zero", "apply"])
    assert args.agent_zero_command == "apply"
    assert args.timeout_seconds == 120.0

    for command in ["apply-tools", "down", "status", "doctor"]:
        args = parser.parse_args(["agent-zero", command])
        assert args.agent_zero_command == command

    args = parser.parse_args(["agent-zero", "reset", "configuration", "--yes"])
    assert args.scope == "configuration"
    assert args.yes is True
