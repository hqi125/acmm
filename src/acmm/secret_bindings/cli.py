from __future__ import annotations

import argparse
import sys

from acmm.secret_bindings import (
    CURRENT_SECRET_BINDINGS_SCHEMA_VERSION,
    bind_secret_file,
    core_secret_binding_key,
    default_secret_bindings_path,
    default_secret_directory_path,
    experiment_secret_binding_key,
    initialize_secret_store,
    list_secret_bindings,
    secret_binding_error,
    secret_binding_key,
    secret_binding_key_type,
    secret_binding_label,
    unbind_secret,
    upgrade_secret_bindings_registry,
    validate_secret_bindings_registry,
)


def register_secret_commands(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "secret",
        help="Manage local ACMM secret bindings.",
    )
    commands = parser.add_subparsers(dest="secret_command", required=True)

    init_parser = commands.add_parser(
        "init",
        help="Initialize the local ACMM secret directory and registry.",
    )
    init_parser.set_defaults(handler=run_secret_init)

    validate_parser = commands.add_parser(
        "validate",
        help="Validate the registry and every bound local secret source.",
    )
    validate_parser.set_defaults(handler=run_secret_validate)

    upgrade_parser = commands.add_parser(
        "upgrade",
        help="Explicitly upgrade a supported older registry to the current schema.",
    )
    upgrade_parser.set_defaults(handler=run_secret_upgrade)

    bind_parser = commands.add_parser(
        "bind",
        help="Bind one scoped ACMM secret key to a local file.",
    )
    bind_scopes = bind_parser.add_subparsers(dest="secret_scope", required=True)
    _register_bind_scope_commands(bind_scopes)

    unbind_parser = commands.add_parser(
        "unbind",
        help="Remove one exact scoped ACMM secret binding.",
    )
    unbind_scopes = unbind_parser.add_subparsers(dest="secret_scope", required=True)
    _register_unbind_scope_commands(unbind_scopes)

    list_parser = commands.add_parser(
        "list",
        help="List local secret bindings without reading or printing secret values.",
    )
    list_parser.set_defaults(handler=run_secret_list)


def run_secret_init(args: argparse.Namespace) -> int:
    del args
    try:
        state = initialize_secret_store()
    except (secret_binding_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("ACMM secret store:")
    print(f"  directory:        {state.secret_directory}")
    print(f"  bindings:         {state.bindings_path}")
    print(f"  registry created: {'yes' if state.registry_created else 'no'}")
    print(f"  registry schema:  {state.registry_schema_version}")
    if state.registry_is_current:
        print("  compatibility:    current")
    else:
        print(
            "  compatibility:    upgrade required "
            f"(Core requires {CURRENT_SECRET_BINDINGS_SCHEMA_VERSION})"
        )
        print("  next command:     acmm secret upgrade")
    return 0


def run_secret_validate(args: argparse.Namespace) -> int:
    del args
    try:
        version = validate_secret_bindings_registry()
        binding_count = len(list_secret_bindings())
    except (secret_binding_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print("ACMM secret bindings registry is valid:")
    print(f"  bindings: {default_secret_bindings_path()}")
    print(f"  schema:   {version}")
    print(f"  sources:  {binding_count}/{binding_count} usable")
    return 0


def run_secret_upgrade(args: argparse.Namespace) -> int:
    del args
    try:
        result = upgrade_secret_bindings_registry()
    except (secret_binding_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print("ACMM secret bindings registry upgrade:")
    print(f"  bindings: {result.bindings_path}")
    print(f"  from:     {result.source_version}")
    print(f"  to:       {result.target_version}")
    print(f"  changed:  {'yes' if result.changed else 'no'}")
    return 0


def run_secret_bind(args: argparse.Namespace) -> int:
    return _run_bind(
        _key_from_args(args),
        args.file,
        replace=args.replace,
    )


def run_secret_unbind(args: argparse.Namespace) -> int:
    return _run_unbind(_key_from_args(args))


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
            f"  {secret_binding_label(binding.key)} -> file:{binding.source_path}"
        )
    return 0


def _run_bind(
    key: secret_binding_key_type,
    source_file: str,
    *,
    replace: bool,
) -> int:
    registry = default_secret_bindings_path()
    directory = default_secret_directory_path()
    if not registry.is_file() or not directory.is_dir():
        print(
            "ERROR: ACMM secret store is not initialized; run 'acmm secret init' first.",
            file=sys.stderr,
        )
        return 2
    try:
        binding = bind_secret_file(
            key,
            source_file,
            replace=replace,
        )
    except (secret_binding_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("ACMM secret binding saved:")
    print(f"  key:         {secret_binding_label(binding.key)}")
    print(f"  source:      file:{binding.source_path}")
    print(f"  bindings:    {default_secret_bindings_path()}")
    print(f"  secret dir:  {default_secret_directory_path()}")
    print("  value:       <not displayed>")
    return 0


def _run_unbind(key: secret_binding_key_type) -> int:
    try:
        bindings = list_secret_bindings()
    except (secret_binding_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    matching = [binding for binding in bindings if binding.key == key]
    if not matching:
        print(
            f"ERROR: No local secret binding exists for {secret_binding_label(key)}.",
            file=sys.stderr,
        )
        alternatives = [
            binding
            for binding in bindings
            if binding.key.secret_id == key.secret_id
        ]
        if alternatives:
            print(
                "Same secret_id exists under the following scoped key(s):",
                file=sys.stderr,
            )
            for binding in alternatives:
                print(
                    f"  {secret_binding_label(binding.key)}",
                    file=sys.stderr,
                )
        return 2

    try:
        changed = unbind_secret(key)
    except (secret_binding_error, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not changed:
        print(
            f"ERROR: Secret binding disappeared before removal: "
            f"{secret_binding_label(key)}.",
            file=sys.stderr,
        )
        return 2

    print("ACMM secret binding removed.")
    print(f"  key: {secret_binding_label(key)}")
    print("Secret source files are never deleted by unbind.")
    return 0


def _register_bind_scope_commands(
    subparsers: argparse._SubParsersAction,
) -> None:
    core = subparsers.add_parser(
        "core",
        help="Bind one ACMM Core secret.",
    )
    _add_core_key_arguments(core)
    _add_bind_arguments(core)
    core.set_defaults(handler=run_secret_bind)

    experiment = subparsers.add_parser(
        "experiment",
        help="Bind one Experiment Package runtime secret.",
    )
    _add_experiment_key_arguments(experiment)
    _add_bind_arguments(experiment)
    experiment.set_defaults(handler=run_secret_bind)

    integration = subparsers.add_parser(
        "integration",
        help="Bind one Experiment Package integration secret.",
    )
    _add_integration_key_arguments(integration)
    _add_bind_arguments(integration)
    integration.set_defaults(handler=run_secret_bind)


def _register_unbind_scope_commands(
    subparsers: argparse._SubParsersAction,
) -> None:
    core = subparsers.add_parser(
        "core",
        help="Remove one ACMM Core secret binding.",
    )
    _add_core_key_arguments(core)
    core.set_defaults(handler=run_secret_unbind)

    experiment = subparsers.add_parser(
        "experiment",
        help="Remove one Experiment Package runtime secret binding.",
    )
    _add_experiment_key_arguments(experiment)
    experiment.set_defaults(handler=run_secret_unbind)

    integration = subparsers.add_parser(
        "integration",
        help="Remove one Experiment Package integration secret binding.",
    )
    _add_integration_key_arguments(integration)
    integration.set_defaults(handler=run_secret_unbind)


def _add_bind_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--file", required=True, help="Local secret source file.")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing exact scoped binding with a different source file.",
    )


def _add_core_key_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("secret_id")


def _add_experiment_key_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("experiment_id")
    parser.add_argument("secret_id")


def _add_integration_key_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("experiment_id")
    parser.add_argument("integration_id")
    parser.add_argument("secret_id")


def _key_from_args(args: argparse.Namespace) -> secret_binding_key_type:
    if args.secret_scope == "core":
        return core_secret_binding_key(secret_id=args.secret_id)
    if args.secret_scope == "experiment":
        return experiment_secret_binding_key(
            experiment_id=args.experiment_id,
            secret_id=args.secret_id,
        )
    if args.secret_scope == "integration":
        return secret_binding_key(
            experiment_id=args.experiment_id,
            integration_id=args.integration_id,
            secret_id=args.secret_id,
        )
    raise secret_binding_error(f"Unsupported secret binding scope: {args.secret_scope!r}.")


__all__ = ["register_secret_commands"]
