from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from acmm_infra.docker import docker_client, docker_error, require_success
from acmm_infra.hermes import hermes_manager, infrastructure_error
from acmm_infra.profile import (
    default_repo_root,
    default_runtime_root,
    load_hermes_profile,
    profile_error,
)


def _context(args: argparse.Namespace):
    repo_root = Path(args.repo_root).expanduser().resolve()
    runtime_root = Path(args.runtime_root).expanduser().resolve()
    profile = load_hermes_profile(repo_root)
    docker = docker_client(repo_root, runtime_root, profile)
    manager = hermes_manager(repo_root, runtime_root, profile, docker)
    return repo_root, runtime_root, profile, docker, manager


def _run_hermes_init(args: argparse.Namespace) -> int:
    _, _, _, docker, manager = _context(args)
    result = manager.initialize_deployment()
    require_success(docker.compose_config(), "Docker Compose validation")

    print(f"Hermes deployment root: {result['deployment_root']}")
    print(f"  compose.yaml: {result['compose']}")
    print(f"  .env:         {result['env']}")
    print(f"  data:         {result['data_root']}")
    print("Hermes infrastructure initialized.")
    return 0


def _run_hermes_up(args: argparse.Namespace) -> int:
    _, _, _, docker, _ = _context(args)
    require_success(docker.compose_config(), "Docker Compose validation")
    require_success(docker.up(), "Starting Hermes infrastructure")
    print("Hermes infrastructure started.")
    return 0


def _run_hermes_down(args: argparse.Namespace) -> int:
    _, _, _, docker, _ = _context(args)
    require_success(docker.down(), "Stopping Hermes infrastructure")
    print("Hermes infrastructure stopped.")
    return 0


def _run_hermes_apply(args: argparse.Namespace) -> int:
    _, _, _, _, manager = _context(args)

    if args.quiet:
        output_mode = "quiet"
    elif args.verbose:
        output_mode = "verbose"
    else:
        output_mode = "normal"

    if output_mode != "quiet":
        print("Applying Hermes infrastructure baseline...", flush=True)

    result = manager.apply(
        timeout_seconds=args.timeout_seconds,
        progress=lambda message: print(message, flush=True),
        output_mode=output_mode,
    )

    if output_mode == "quiet":
        print("Hermes infrastructure baseline applied.")
        return 0

    print(f"Hermes provider:      {result['model_provider']}")
    print(f"Hermes base URL:      {result['model_base_url']}")
    print(f"Hermes API mode:      {result['model_api_mode']}")
    print(f"Hermes default model: {result['model_default']}")
    print(f"Hermes context:       {result['context_window']}")
    print(f"Hermes reasoning:     {result['reasoning_effort']}")
    print(f"Hermes terminal:      {result['terminal_backend']}")
    print(
        "Hermes Blank Slate:   "
        + ("enabled" if result["blank_slate"] else "disabled")
    )
    print(
        "Baseline toolsets:     "
        + ", ".join(result["blank_slate_toolsets"])
    )
    print("Synchronized plugins: " + ", ".join(result["plugins_synchronized"]))
    print(f"Batch operations:     {result['provisioning_operations']}")
    print("Plugin tool override: denied unless explicitly enabled in profile")
    print("Configuration path:   one bounded provisioning container")
    return 0


def _run_hermes_status(args: argparse.Namespace) -> int:
    _, _, _, _, manager = _context(args)
    status = manager.status()

    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
        return 0

    desired = status["desired"]
    observed = status["observed"]

    print("Hermes infrastructure")
    print(f"  runtime root:      {status['runtime_root']}")
    print(f"  deployment root:   {status['deployment_root']}")
    print(f"  compose:           {status['deployment']['compose_path']}")
    print(f"  compose baseline:  {status['deployment']['compose_matches_default']}")
    print(f"  env:               {status['deployment']['env_path']}")
    print(f"  data root:         {status['data_root']}")
    print(f"  container:         {desired['container_name']}")
    print(f"  running:           {observed['container_running']}")
    print(f"  image observed:    {observed['container_image']}")
    print(f"  version observed:  {observed['hermes_version']}")
    print(f"  version expected:  {desired['hermes_version']}")
    print(f"  provider observed: {observed['model_provider']}")
    print(f"  provider expected: {desired['model_provider']}")
    print(f"  base URL observed: {observed['model_base_url']}")
    print(f"  base URL expected: {desired['model_base_url']}")
    print(f"  API mode observed: {observed['model_api_mode']}")
    print(f"  API mode expected: {desired['model_api_mode']}")
    print(f"  model observed:    {observed['model_default']}")
    print(f"  model expected:    {desired['model_default']}")
    print(f"  context observed:  {observed['context_window']}")
    print(f"  context expected:  {desired['context_window']}")
    print(f"  reasoning observed:{observed['reasoning_effort']}")
    print(f"  reasoning expected:{desired['reasoning_effort']}")
    print(f"  terminal observed: {observed['terminal_backend']}")
    print(f"  terminal expected: {desired['terminal_backend']}")

    blank = desired["blank_slate"]
    print(f"  Blank Slate:       {blank['enabled']}")
    print(f"  toolsets observed: {observed['platform_toolsets_cli']}")
    print(f"  toolsets expected: {blank['minimal_toolsets']}")
    print(f"  max turns observed:{observed['max_turns']}")
    print(f"  max turns expected:{blank['max_turns']}")
    print(f"  memory observed:   {observed['memory_enabled']}")
    print(f"  memory expected:   {blank['memory_enabled']}")
    print(
        "  bundled opt-out:   "
        f"{observed['bundled_skills_opt_out']}"
    )
    print(
        "  bundled skills:    "
        f"{observed['bundled_skill_manifest']}"
    )
    print(f"  overlay toolsets:   {blank['overlay_toolsets']}")
    print(f"  config path:       {observed['config_path']}")
    print(f"  env path:          {observed['env_path']}")

    for plugin in status["plugins"]:
        print(
            f"  plugin {plugin['name']}: "
            f"sync={plugin['synchronized']} "
            f"enabled={plugin['enabled_observed']}"
        )

    return 0


def _run_hermes_doctor(args: argparse.Namespace) -> int:
    _, _, _, _, manager = _context(args)
    report = manager.doctor()

    if args.json:
        payload = {
            "ready": report.is_ready,
            "checks": [
                {
                    "name": check.name,
                    "ok": check.ok,
                    "detail": check.detail,
                }
                for check in report.checks
            ],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for check in report.checks:
            state = "OK" if check.ok else "FAIL"
            print(f"[{state}] {check.name}: {check.detail}")

        print("READY" if report.is_ready else "NOT READY")

    return 0 if report.is_ready else 1




def _run_hermes_adopt_data(args: argparse.Namespace) -> int:
    if not args.yes:
        print(
            "Refusing data adoption without --yes. Existing Hermes runtime data may "
            "contain credentials and other private state.",
            file=sys.stderr,
        )
        return 2

    _, _, _, _, manager = _context(args)
    destination = manager.adopt_data(args.source)
    print(f"Hermes data adopted into: {destination}")
    print("The source directory was preserved unchanged.")
    return 0

def _run_hermes_reset(args: argparse.Namespace) -> int:
    if not args.yes:
        print(
            "Refusing reset without --yes. The reset is scoped, but it still "
            "deletes mutable Hermes state.",
            file=sys.stderr,
        )
        return 2

    _, _, _, _, manager = _context(args)
    removed = manager.reset(args.scope)

    if not removed:
        print(f"Hermes reset scope '{args.scope}' was already clean.")
        return 0

    print(f"Hermes reset scope '{args.scope}':")
    for path in removed:
        print(f"  removed {path}")
    return 0


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

    hermes_parser = subparsers.add_parser("hermes", help="Manage Hermes infrastructure.")
    hermes_subparsers = hermes_parser.add_subparsers(
        dest="hermes_command",
        required=True,
    )

    init_parser = hermes_subparsers.add_parser(
        "init",
        help="Initialize a self-contained Hermes deployment.",
    )
    init_parser.set_defaults(handler=_run_hermes_init)

    up_parser = hermes_subparsers.add_parser(
        "up",
        help="Start the initialized Hermes deployment.",
    )
    up_parser.set_defaults(handler=_run_hermes_up)

    down_parser = hermes_subparsers.add_parser(
        "down",
        help="Stop the initialized Hermes deployment.",
    )
    down_parser.set_defaults(handler=_run_hermes_down)

    apply_parser = hermes_subparsers.add_parser(
        "apply",
        help="Reconcile Hermes with the version-controlled profile.",
    )
    apply_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=120.0,
        help="Maximum duration of the single Hermes provisioning batch (default: 120).",
    )
    verbosity_group = apply_parser.add_mutually_exclusive_group()
    verbosity_group.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Show only the final success message or an error.",
    )
    verbosity_group.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show raw Docker/container/Hermes provisioning output.",
    )
    apply_parser.set_defaults(handler=_run_hermes_apply)

    status_parser = hermes_subparsers.add_parser(
        "status",
        help="Show desired and observed Hermes state.",
    )
    status_parser.add_argument("--json", action="store_true")
    status_parser.set_defaults(handler=_run_hermes_status)

    doctor_parser = hermes_subparsers.add_parser(
        "doctor",
        help="Check whether Hermes is ready for ACMM experiments.",
    )
    doctor_parser.add_argument("--json", action="store_true")
    doctor_parser.set_defaults(handler=_run_hermes_doctor)


    adopt_parser = hermes_subparsers.add_parser(
        "adopt-data",
        help="Copy an existing Hermes data directory into the new runtime root.",
    )
    adopt_parser.add_argument("source")
    adopt_parser.add_argument("--yes", action="store_true")
    adopt_parser.set_defaults(handler=_run_hermes_adopt_data)

    reset_parser = hermes_subparsers.add_parser(
        "reset",
        help="Delete one configured scope of mutable Hermes state.",
    )
    reset_parser.add_argument("scope")
    reset_parser.add_argument("--yes", action="store_true")
    reset_parser.set_defaults(handler=_run_hermes_reset)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        return args.handler(args)
    except KeyboardInterrupt:
        print("ERROR: operation interrupted; temporary provisioning state was cleaned up.", file=sys.stderr)
        return 130
    except (profile_error, infrastructure_error, docker_error, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
