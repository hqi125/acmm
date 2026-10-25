from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from acmm_infra.commands.agent import agent_cli
from acmm_infra.agent_zero import agent_zero_manager
from acmm_infra.deployment import active_experiment_id
from acmm_infra.docker import docker_client, require_success
from acmm_infra.profiles.agent_zero import load_agent_zero_profile
from acmm_infra.profiles.experiments import with_agent_zero_experiment


class agent_zero_cli(agent_cli):
    @property
    def command_name(self) -> str:
        return "agent-zero"

    @property
    def help(self) -> str:
        return "Manage Agent Zero infrastructure."

    def _context_for_experiment(
        self,
        args: argparse.Namespace,
        experiment_id: str | None,
    ):
        repo_root = Path(args.repo_root).expanduser().resolve()
        runtime_root = Path(args.runtime_root).expanduser().resolve()
        profile = load_agent_zero_profile(repo_root)
        if experiment_id is not None:
            profile = with_agent_zero_experiment(repo_root, profile, experiment_id)
        docker = docker_client(repo_root, runtime_root, profile)
        manager = agent_zero_manager(
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
        baseline = load_agent_zero_profile(repo_root)
        deployment_root = (
            runtime_root / baseline.deployment_directory
        ).resolve()
        experiment_id = active_experiment_id(deployment_root)
        return self._context_for_experiment(args, experiment_id)

    def configure_up_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--timeout-seconds",
            type=float,
            default=120.0,
            help="Maximum time to wait for the Agent Zero web backend to become ready.",
        )

    def configure_apply_parser(self, parser: argparse.ArgumentParser) -> None:
        super().configure_apply_parser(parser)
        parser.add_argument(
            "--timeout-seconds",
            type=float,
            default=120.0,
            help="Maximum time to wait for temporary provisioning startup.",
        )

    def configure_apply_experiment_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("experiment_id")
        parser.add_argument(
            "--timeout-seconds",
            type=float,
            default=120.0,
            help="Maximum time to wait for temporary provisioning startup.",
        )

    def configure_apply_tools_parser(self, parser: argparse.ArgumentParser) -> None:
        super().configure_apply_tools_parser(parser)
        parser.add_argument(
            "--timeout-seconds",
            type=float,
            default=120.0,
            help="Maximum time to wait for temporary tool provisioning startup.",
        )

    def run_init(self, args: argparse.Namespace) -> int:
        _, _, _, docker, manager = self._context(args)
        result = manager.initialize_deployment()
        require_success(docker.compose_config(), "Docker Compose validation")
        print(f"Agent Zero deployment root: {result['deployment_root']}")
        print(f"  compose.yaml: {result['compose']}")
        print(f"  .env:         {result['env']}")
        print(f"  usr:          {result['data_root']}")
        print("Agent Zero infrastructure initialized.")
        return 0

    def run_up(self, args: argparse.Namespace) -> int:
        _, _, _, _, manager = self._context(args)
        manager.up(timeout_seconds=args.timeout_seconds)
        print("Agent Zero infrastructure started and web backend is ready.")
        return 0

    def run_down(self, args: argparse.Namespace) -> int:
        _, _, _, docker, _ = self._context(args)
        require_success(docker.down(), "Stopping Agent Zero infrastructure")
        print("Agent Zero infrastructure stopped.")
        return 0

    def run_apply(self, args: argparse.Namespace) -> int:
        experiment_id = args.experiment_id
        if experiment_id is None:
            print(
                "WARNING: no experiment selected; applying the "
                "experiment-independent Agent Zero baseline and removing active "
                "experiment apparatus.",
                file=sys.stderr,
            )

        _, _, _, _, manager = self._context_for_experiment(args, experiment_id)
        if experiment_id is None:
            print("Applying Agent Zero baseline...", flush=True)
        else:
            print(
                "Applying Agent Zero environment for experiment "
                f"'{experiment_id}'...",
                flush=True,
            )
        result = manager.apply(
            timeout_seconds=args.timeout_seconds,
            progress=lambda message: print(message, flush=True),
        )
        if experiment_id is None:
            print("Agent Zero baseline applied.")
        else:
            print(f"Agent Zero experiment environment applied: {experiment_id}")

        print(f"Agent Zero image:       {result['image']}")
        print(f"Agent profile:          {result['agent_profile']}")
        print(f"Model preset:           {result['model_preset']}")
        print(f"Main model provider:    {result['model_provider']}")
        print(f"Main model base URL:    {result['model_base_url']}")
        print(f"Experiment model:       {result['model_default']}")
        print(f"Context window:         {result['context_window']}")
        print(f"Utility model provider: {result['utility_model_provider']}")
        print(f"Utility model:          {result['utility_model_default']}")
        print(
            "Managed plugins:        "
            + (
                ", ".join(result["plugins_synchronized"])
                if result["plugins_synchronized"]
                else "none"
            )
        )
        file_states = set(result["application_files"].values())
        print(
            "Application state:      "
            + ("unchanged" if file_states == {"unchanged"} else "reconciled")
        )
        print(f"Model preset state:     {result['model_preset_state']}")
        print(f"Persistent settings:    {result['persistent_settings']}")
        print(f"Provisioning service:   {result['provisioning_service']}")

        if experiment_id is not None:
            print(f"Experiment:             {result['experiment_id']}")
            print(f"Apparatus SHA-256:      {result['source_sha256']}")
            print("Application scope:      experiment")
        return 0

    def run_apply_experiment(self, args: argparse.Namespace) -> int:
        return self.run_apply(args)

    def run_apply_tools(self, args: argparse.Namespace) -> int:
        experiment_id = args.experiment_id
        if experiment_id is None:
            print(
                "WARNING: no experiment selected; reconciling Agent Zero "
                "baseline tool policy only.",
                file=sys.stderr,
            )

        _, _, _, _, manager = self._context_for_experiment(args, experiment_id)
        result = manager.apply_tools(
            experiment_id,
            timeout_seconds=args.timeout_seconds,
        )
        if experiment_id is None:
            print("Agent Zero baseline tool policy applied.")
        else:
            print(f"Agent Zero experiment tool apparatus applied: {experiment_id}")
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
        print("Agent Zero infrastructure")
        print(f"  runtime root:      {status['runtime_root']}")
        print(f"  deployment root:   {status['deployment_root']}")
        apparatus = status["experiment_apparatus"]
        if apparatus is None:
            print("  active experiment: none")
        else:
            print(f"  active experiment: {apparatus.get('experiment_id')}")
            print(f"  application scope: {apparatus.get('application_scope')}")
            print(f"  apparatus SHA-256: {apparatus.get('source_sha256')}")
        print(f"  compose baseline:  {status['deployment']['compose_matches_default']}")
        print(f"  container:         {desired['container_name']}")
        print(f"  running:           {observed['container_running']}")
        print(f"  image observed:    {observed['container_image']}")
        print(f"  image expected:    {desired['image']}")
        print(f"  version observed:  {observed['version']}")
        print(f"  version expected:  {desired['version']}")
        print(f"  agent profile:     {desired['agent_profile']}")
        print(f"  model preset:      {desired['model_preset']}")
        print(f"  model provider:    {desired['model_provider']}")
        print(f"  model base URL:    {desired['model_base_url']}")
        print(f"  experiment model:  {desired['model_default']}")
        print(f"  context window:    {desired['context_window']}")
        application_exact = all(
            item["exact"] for item in observed["application_files"].values()
        )
        print(f"  application files: exact={application_exact}")
        for name, plugin in observed["managed_plugins"].items():
            print(
                f"  plugin {name}: "
                f"present={plugin['present']} exact={plugin['exact']}"
            )
        live = observed["live_application"]
        if live is not None:
            print("  application API:")
            print(f"    settings exact:  {live['settings_exact']}")
            print(f"    model exact:     {live['model_config_exact']}")
            print(f"    model available: {live['model_available']}")
            if live["models_observed"]:
                print("    models observed: " + ", ".join(live["models_observed"]))
            if live["error"]:
                print(f"    error:           {live['error']}")
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

    def run_reset(self, args: argparse.Namespace) -> int:
        if not args.yes:
            print(
                "Refusing reset without --yes. The reset deletes mutable Agent Zero state.",
                file=sys.stderr,
            )
            return 2

        _, _, _, _, manager = self._context(args)
        removed = manager.reset(args.scope)
        if not removed:
            print(f"Agent Zero reset scope '{args.scope}' was already clean.")
            return 0

        print(f"Agent Zero reset scope '{args.scope}':")
        for path in removed:
            print(f"  removed {path}")
        return 0
