from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from acmm_infra.commands.agent import agent_cli, command_definition
from acmm_infra.deployment import active_experiment_id
from acmm_infra.docker import docker_client, require_success
from acmm_infra.hermes import hermes_manager
from acmm_infra.profiles.experiments import with_hermes_experiment
from acmm_infra.profiles.hermes import load_hermes_profile


class hermes_cli(agent_cli):
    @property
    def command_name(self) -> str:
        return "hermes"

    @property
    def help(self) -> str:
        return "Manage Hermes infrastructure."

    def extra_command_definitions(self) -> tuple[command_definition, ...]:
        return (
            command_definition(
                "adopt-data",
                "Copy an existing Hermes data directory into the runtime root.",
                "run_adopt_data",
                "configure_adopt_data_parser",
            ),
        )

    def _context_for_experiment(
        self,
        args: argparse.Namespace,
        experiment_id: str | None,
    ):
        repo_root = Path(args.repo_root).expanduser().resolve()
        runtime_root = Path(args.runtime_root).expanduser().resolve()
        profile = load_hermes_profile(repo_root)
        if experiment_id is not None:
            profile = with_hermes_experiment(repo_root, profile, experiment_id)
        docker = docker_client(repo_root, runtime_root, profile)
        manager = hermes_manager(
            repo_root,
            runtime_root,
            profile,
            docker,
            experiment_id=experiment_id,
        )
        return repo_root, runtime_root, profile, docker, manager

    def _context(self, args: argparse.Namespace):
        return self._context_for_experiment(
            args,
            getattr(args, "experiment_id", None),
        )

    def _deployed_context(self, args: argparse.Namespace):
        repo_root = Path(args.repo_root).expanduser().resolve()
        runtime_root = Path(args.runtime_root).expanduser().resolve()
        baseline = load_hermes_profile(repo_root)
        deployment_root = (
            runtime_root / baseline.deployment_directory
        ).resolve()
        experiment_id = active_experiment_id(deployment_root)
        return self._context_for_experiment(args, experiment_id)

    def configure_apply_parser(self, parser: argparse.ArgumentParser) -> None:
        super().configure_apply_parser(parser)
        self._configure_provisioning_parser(
            parser,
            "Maximum duration of the single Hermes provisioning batch "
            "(default: 120).",
        )

    def configure_apply_experiment_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("experiment_id")
        self._configure_provisioning_parser(
            parser,
            "Maximum duration of the experiment-apparatus provisioning batch "
            "(default: 120).",
        )

    def configure_apply_tools_parser(self, parser: argparse.ArgumentParser) -> None:
        super().configure_apply_tools_parser(parser)
        self._configure_provisioning_parser(
            parser,
            "Maximum duration of the tool-policy provisioning batch "
            "(default: 120).",
        )

    def configure_adopt_data_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("source")
        parser.add_argument("--yes", action="store_true")

    def _configure_provisioning_parser(
        self,
        parser: argparse.ArgumentParser,
        timeout_help: str,
    ) -> None:
        parser.add_argument(
            "--timeout-seconds",
            type=float,
            default=120.0,
            help=timeout_help,
        )
        verbosity_group = parser.add_mutually_exclusive_group()
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

    @staticmethod
    def _provisioning_output_mode(args: argparse.Namespace) -> str:
        if args.quiet:
            return "quiet"
        if args.verbose:
            return "verbose"
        return "normal"

    def run_init(self, args: argparse.Namespace) -> int:
        _, _, _, docker, manager = self._context(args)
        result = manager.initialize_deployment()
        require_success(docker.compose_config(), "Docker Compose validation")

        print(f"Hermes deployment root: {result['deployment_root']}")
        print(f"  compose.yaml: {result['compose']}")
        print(f"  .env:         {result['env']}")
        print(f"  data:         {result['data_root']}")
        print("Hermes infrastructure initialized.")
        return 0

    def run_up(self, args: argparse.Namespace) -> int:
        _, _, _, docker, _ = self._context(args)
        require_success(docker.compose_config(), "Docker Compose validation")
        require_success(docker.up(), "Starting Hermes infrastructure")
        print("Hermes infrastructure started.")
        return 0

    def run_down(self, args: argparse.Namespace) -> int:
        _, _, _, docker, _ = self._context(args)
        require_success(docker.down(), "Stopping Hermes infrastructure")
        print("Hermes infrastructure stopped.")
        return 0

    def run_apply(self, args: argparse.Namespace) -> int:
        experiment_id = args.experiment_id
        if experiment_id is None:
            print(
                "WARNING: no experiment selected; applying the "
                "experiment-independent Hermes baseline and removing active "
                "experiment apparatus.",
                file=sys.stderr,
            )

        _, _, _, _, manager = self._context_for_experiment(args, experiment_id)
        output_mode = self._provisioning_output_mode(args)

        if output_mode != "quiet":
            if experiment_id is None:
                print("Applying Hermes baseline...", flush=True)
            else:
                print(
                    "Applying Hermes environment for experiment "
                    f"'{experiment_id}'...",
                    flush=True,
                )

        result = manager.apply(
            timeout_seconds=args.timeout_seconds,
            progress=lambda message: print(message, flush=True),
            output_mode=output_mode,
        )

        if output_mode == "quiet":
            if experiment_id is None:
                print("Hermes baseline applied.")
            else:
                print(f"Hermes experiment environment applied: {experiment_id}")
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
        isolation = result["experiment_isolation"]
        print(
            "ACMM title generation: "
            f"{isolation['title_generation_enabled']}"
        )
        print(
            "ACMM background review:"
            f" {isolation['background_review_enabled']}"
        )
        print("ACMM tool search:      " f"{isolation['tool_search_enabled']}")
        print("Synchronized plugins: " + ", ".join(result["plugins_synchronized"]))
        print(f"Batch operations:     {result['provisioning_operations']}")
        print("Plugin tool override: denied unless explicitly enabled in profile")
        print("Configuration path:   one bounded provisioning container")

        if experiment_id is not None:
            print(f"Experiment:           {result['experiment_id']}")
            print(f"Apparatus SHA-256:    {result['source_sha256']}")
            print("Application scope:    experiment")
        return 0

    def run_apply_experiment(self, args: argparse.Namespace) -> int:
        return self.run_apply(args)

    def run_apply_tools(self, args: argparse.Namespace) -> int:
        experiment_id = args.experiment_id
        if experiment_id is None:
            print(
                "WARNING: no experiment selected; reconciling Hermes baseline "
                "tool policy only.",
                file=sys.stderr,
            )

        _, _, _, _, manager = self._context_for_experiment(args, experiment_id)
        output_mode = self._provisioning_output_mode(args)
        result = manager.apply_tools(
            experiment_id,
            timeout_seconds=args.timeout_seconds,
            progress=lambda message: print(message, flush=True),
            output_mode=output_mode,
        )

        if experiment_id is None:
            print("Hermes baseline tool policy applied.")
        else:
            print(f"Hermes experiment tool apparatus applied: {experiment_id}")
            if output_mode != "quiet":
                print(f"  apparatus SHA-256: {result['source_sha256']}")
                print("  application scope: tools")
        return 0

    def run_status(self, args: argparse.Namespace) -> int:
        _, _, _, _, manager = self._deployed_context(args)
        status = manager.status()

        if args.json:
            print(json.dumps(status, indent=2, sort_keys=True))
            return 0

        desired = status["desired"]
        observed = status["observed"]

        print("Hermes infrastructure")
        print(f"  runtime root:      {status['runtime_root']}")
        print(f"  deployment root:   {status['deployment_root']}")
        apparatus = status["experiment_apparatus"]
        if apparatus is None:
            print("  active experiment: none")
        else:
            print(f"  active experiment: {apparatus.get('experiment_id')}")
            print(f"  application scope: {apparatus.get('application_scope')}")
            print(f"  apparatus SHA-256: {apparatus.get('source_sha256')}")
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

        isolation = desired["experiment_isolation"]
        print("  ACMM experiment isolation:")
        print(
            "    title generation:  "
            f"{observed['title_generation_enabled']} "
            f"(expected {isolation['title_generation_enabled']})"
        )
        print(
            "    background review: "
            f"{observed['background_review_enabled']} "
            f"(expected {isolation['background_review_enabled']})"
        )
        print(
            "    tool search:       "
            f"{observed['tool_search_enabled']} "
            f"(expected {isolation['tool_search_enabled']})"
        )

        blank = desired["blank_slate"]
        print(f"  Blank Slate:       {blank['enabled']}")
        print(f"  toolsets observed: {observed['platform_toolsets_cli']}")
        print(f"  toolsets expected: {blank['minimal_toolsets']}")
        print(f"  max turns observed:{observed['max_turns']}")
        print(f"  max turns expected:{blank['max_turns']}")
        print(f"  memory observed:   {observed['memory_enabled']}")
        print(f"  memory expected:   {blank['memory_enabled']}")
        print("  bundled opt-out:   " f"{observed['bundled_skills_opt_out']}")
        print("  bundled skills:    " f"{observed['bundled_skill_manifest']}")
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

    def run_doctor(self, args: argparse.Namespace) -> int:
        _, _, _, _, manager = self._deployed_context(args)
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

    def run_adopt_data(self, args: argparse.Namespace) -> int:
        if not args.yes:
            print(
                "Refusing data adoption without --yes. Existing Hermes runtime "
                "data may contain credentials and other private state.",
                file=sys.stderr,
            )
            return 2

        _, _, _, _, manager = self._context(args)
        destination = manager.adopt_data(args.source)
        print(f"Hermes data adopted into: {destination}")
        print("The source directory was preserved unchanged.")
        return 0

    def run_reset(self, args: argparse.Namespace) -> int:
        if not args.yes:
            print(
                "Refusing reset without --yes. The reset is scoped, but it still "
                "deletes mutable Hermes state.",
                file=sys.stderr,
            )
            return 2

        _, _, _, _, manager = self._context(args)
        removed = manager.reset(args.scope)

        if not removed:
            print(f"Hermes reset scope '{args.scope}' was already clean.")
            return 0

        print(f"Hermes reset scope '{args.scope}':")
        for path in removed:
            print(f"  removed {path}")
        return 0
