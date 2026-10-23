from __future__ import annotations

from abc import ABC, abstractmethod
import argparse
from dataclasses import dataclass


@dataclass(frozen=True)
class command_definition:
    name: str
    help: str
    handler_method: str
    configure_method: str | None = None


STANDARD_AGENT_COMMANDS = (
    command_definition(
        "init",
        "Initialize a self-contained deployment.",
        "run_init",
    ),
    command_definition(
        "up",
        "Start the initialized deployment.",
        "run_up",
        "configure_up_parser",
    ),
    command_definition(
        "down",
        "Stop the initialized deployment.",
        "run_down",
    ),
    command_definition(
        "apply",
        "Reconcile the deployment with the version-controlled baseline.",
        "run_apply",
        "configure_apply_parser",
    ),
    command_definition(
        "apply-tools",
        "Reconcile repository-managed agent tools.",
        "run_apply_tools",
        "configure_apply_tools_parser",
    ),
    command_definition(
        "status",
        "Show desired and observed infrastructure state.",
        "run_status",
        "configure_status_parser",
    ),
    command_definition(
        "doctor",
        "Check whether the deployment is ready for ACMM experiments.",
        "run_doctor",
        "configure_doctor_parser",
    ),
    command_definition(
        "reset",
        "Delete one configured scope of mutable runtime state.",
        "run_reset",
        "configure_reset_parser",
    ),
)


class agent_cli(ABC):
    @property
    @abstractmethod
    def command_name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def help(self) -> str:
        raise NotImplementedError

    def command_definitions(self) -> tuple[command_definition, ...]:
        return STANDARD_AGENT_COMMANDS + self.extra_command_definitions()

    def extra_command_definitions(self) -> tuple[command_definition, ...]:
        return ()

    def register(self, subparsers: argparse._SubParsersAction) -> None:
        parser = subparsers.add_parser(self.command_name, help=self.help)
        destination = self.command_name.replace("-", "_") + "_command"
        agent_subparsers = parser.add_subparsers(dest=destination, required=True)

        for definition in self.command_definitions():
            command_parser = agent_subparsers.add_parser(
                definition.name,
                help=definition.help,
            )

            if definition.configure_method is not None:
                configure = getattr(self, definition.configure_method)
                configure(command_parser)

            handler = getattr(self, definition.handler_method)
            command_parser.set_defaults(handler=handler)

    def configure_up_parser(self, parser: argparse.ArgumentParser) -> None:
        del parser

    def configure_apply_parser(self, parser: argparse.ArgumentParser) -> None:
        del parser

    def configure_apply_tools_parser(self, parser: argparse.ArgumentParser) -> None:
        del parser

    def configure_status_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--json", action="store_true")

    def configure_doctor_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--json", action="store_true")

    def configure_reset_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("scope")
        parser.add_argument("--yes", action="store_true")

    @abstractmethod
    def run_init(self, args: argparse.Namespace) -> int:
        raise NotImplementedError

    @abstractmethod
    def run_up(self, args: argparse.Namespace) -> int:
        raise NotImplementedError

    @abstractmethod
    def run_down(self, args: argparse.Namespace) -> int:
        raise NotImplementedError

    @abstractmethod
    def run_apply(self, args: argparse.Namespace) -> int:
        raise NotImplementedError

    @abstractmethod
    def run_apply_tools(self, args: argparse.Namespace) -> int:
        raise NotImplementedError

    @abstractmethod
    def run_status(self, args: argparse.Namespace) -> int:
        raise NotImplementedError

    @abstractmethod
    def run_doctor(self, args: argparse.Namespace) -> int:
        raise NotImplementedError

    @abstractmethod
    def run_reset(self, args: argparse.Namespace) -> int:
        raise NotImplementedError
