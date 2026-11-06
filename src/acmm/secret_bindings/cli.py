from __future__ import annotations

import argparse
import sys

from acmm.secret_bindings import (
    bind_secret_file,
    default_secret_bindings_path,
    list_secret_bindings,
    secret_binding_error,
    secret_binding_key,
    unbind_secret,
)


def register_secret_commands(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "secret",
        help="Manage local secret bindings used by Experiment Package integrations.",
    )
    commands = parser.add_subparsers(dest="secret_command", required=True)

    bind_parser = commands.add_parser(
        "bind",
        help="Bind one package integration secret slot to a local file.",
    )
    _add_key_arguments(bind_parser)
    bind_parser.add_argument("--file", required=True, help="Local secret source file.")
    bind_parser.set_defaults(handler=run_secret_bind)

    unbind_parser = commands.add_parser(
        "unbind",
        help="Remove one local package integration secret binding.",
    )
    _add_key_arguments(unbind_parser)
    unbind_parser.set_defaults(handler=run_secret_unbind)

    list_parser = commands.add_parser(
        "list",
        help="List local secret bindings without reading or printing secret values.",
    )
    list_parser.set_defaults(handler=run_secret_list)


def run_secret_bind(args: argparse.Namespace) -> int:
    try:
        binding = bind_secret_file(_key(args), args.file)
    except (secret_binding_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("ACMM secret binding saved:")
    print(f"  experiment:  {binding.key.experiment_id}")
    print(f"  integration: {binding.key.integration_id}")
    print(f"  secret:      {binding.key.secret_id}")
    print(f"  source:      file:{binding.source_path}")
    print(f"  bindings:    {default_secret_bindings_path()}")
    print("  value:       <not displayed>")
    return 0


def run_secret_unbind(args: argparse.Namespace) -> int:
    try:
        changed = unbind_secret(_key(args))
    except (secret_binding_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print("ACMM secret binding " + ("removed." if changed else "was not present."))
    return 0


def run_secret_list(args: argparse.Namespace) -> int:
    del args
    try:
        bindings = list_secret_bindings()
    except (secret_binding_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"ACMM secret bindings: {default_secret_bindings_path()}")
    if not bindings:
        print("  none")
        return 0
    for binding in bindings:
        print(
            "  "
            f"{binding.key.experiment_id}:{binding.key.integration_id}:"
            f"{binding.key.secret_id} -> file:{binding.source_path}"
        )
    return 0


def _add_key_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("experiment_id")
    parser.add_argument("integration_id")
    parser.add_argument("secret_id")


def _key(args: argparse.Namespace) -> secret_binding_key:
    return secret_binding_key(
        experiment_id=args.experiment_id,
        integration_id=args.integration_id,
        secret_id=args.secret_id,
    )


__all__ = ["register_secret_commands"]
