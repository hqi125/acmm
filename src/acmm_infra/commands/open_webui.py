from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from acmm_infra.commands.agent import agent_cli
from acmm_infra.docker import docker_client, require_success
from acmm_infra.open_webui import open_webui_manager
from acmm_infra.profiles.open_webui import load_open_webui_profile

from acmm_infra.subject_managers import build_deployed_subject_manager

class open_webui_cli(agent_cli):
    @property
    def command_name(self) -> str:
        return "open-webui"

    @property
    def help(self) -> str:
        return "Manage Open WebUI infrastructure."

    def _context(self, args: argparse.Namespace):
        repo_root = Path(args.repo_root).expanduser().resolve()
        runtime_root = Path(args.runtime_root).expanduser().resolve()
        profile = load_open_webui_profile(repo_root)
        docker = docker_client(repo_root, runtime_root, profile)
        manager = open_webui_manager(repo_root, runtime_root, profile, docker)
        return repo_root, runtime_root, profile, docker, manager

    def _deployed_context(self, args: argparse.Namespace):
        repo_root = Path(args.repo_root).expanduser().resolve()
        runtime_root = Path(args.runtime_root).expanduser().resolve()
        context = build_deployed_subject_manager(
            repo_root,
            runtime_root,
            "open-webui",
        )
        manager = context.manager
        return repo_root, runtime_root, manager.profile, manager.docker, manager

    def configure_up_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--timeout-seconds",
            type=float,
            default=60.0,
            help="Maximum time to wait for the Open WebUI backend to become ready.",
        )

    def configure_apply_parser(self, parser: argparse.ArgumentParser) -> None:
        super().configure_apply_parser(parser)
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
            default=60.0,
            help="Maximum time to wait for the running backend to become ready.",
        )

    def run_init(self, args: argparse.Namespace) -> int:
        _, _, _, docker, manager = self._context(args)
        result = manager.initialize_deployment()
        require_success(docker.compose_config(), "Docker Compose validation")
        print(f"Open WebUI deployment root: {result['deployment_root']}")
        print(f"  compose.yaml: {result['compose']}")
        print(f"  .env:         {result['env']}")
        print(f"  data:         {result['data_root']}")
        print("Open WebUI infrastructure initialized.")
        return 0

    def run_up(self, args: argparse.Namespace) -> int:
        _, _, _, _, manager = self._context(args)
        manager.up(timeout_seconds=args.timeout_seconds)
        print("Open WebUI infrastructure started and backend is ready.")
        return 0

    def run_down(self, args: argparse.Namespace) -> int:
        _, _, _, docker, _ = self._context(args)
        require_success(docker.down(), "Stopping Open WebUI infrastructure")
        print("Open WebUI infrastructure stopped.")
        return 0

    def run_apply(self, args: argparse.Namespace) -> int:
        experiment_id = None
        _, _, _, _, manager = self._context(args)
        if experiment_id is None:
            print("Applying Open WebUI baseline...", flush=True)
        else:
            print(
                "Applying Open WebUI environment for experiment "
                f"'{experiment_id}'...",
                flush=True,
            )
        result = manager.apply(
            timeout_seconds=args.timeout_seconds,
            progress=lambda message: print(message, flush=True),
        )
        if experiment_id is None:
            print("Open WebUI baseline applied.")
        else:
            print(f"Open WebUI experiment environment applied: {experiment_id}")

        print(f"Open WebUI image:       {result['image']}")
        print(f"Ollama base URL:        {result['ollama_base_url']}")
        print(f"Experiment model:       {result['model_default']}")
        print(f"Web UI authentication:  {result['webui_auth']}")
        print(f"Secret key file:        {result['secret_key_file']}")
        print(f"Ollama API enabled:     {result['ollama_api_enabled']}")
        print(f"OpenAI API enabled:     {result['openai_api_enabled']}")
        print(f"Plugins enabled:        {result['plugins_enabled']}")
        print(
            "Runtime pip installs:   "
            f"{result['pip_install_frontmatter_requirements_enabled']}"
        )
        print(f"Telemetry enabled:      {result['telemetry_enabled']}")
        application = result["application"]
        print(
            "Persistent Ollama:     "
            + ("updated" if application["ollama_changed"] else "unchanged")
        )
        print(
            "Persistent OpenAI:     "
            + ("updated" if application["openai_changed"] else "unchanged")
        )
        print(
            "Experiment isolation: "
            + ("updated" if application["isolation_changed"] else "unchanged")
        )
        tools = result["tools"]
        print(
            "Managed tools created: "
            + (", ".join(tools["created"]) if tools["created"] else "none")
        )
        print(
            "Managed tools updated: "
            + (", ".join(tools["updated"]) if tools["updated"] else "none")
        )
        print(
            "Managed tools stable:  "
            + (", ".join(tools["unchanged"]) if tools["unchanged"] else "none")
        )
        print("Provisioning service:   stopped after apply")

        if experiment_id is not None:
            print(f"Experiment:             {result['experiment_id']}")
            print(f"Apparatus SHA-256:      {result['source_sha256']}")
            print("Application scope:      experiment")
        return 0

    def run_apply_tools(self, args: argparse.Namespace) -> int:
        experiment_id = None
        _, _, _, _, manager = self._context(args)
        result = manager.apply_tools(
            experiment_id,
            timeout_seconds=args.timeout_seconds,
        )
        if experiment_id is None:
            print("Open WebUI baseline tool policy applied.")
        else:
            print(f"Open WebUI experiment tool apparatus applied: {experiment_id}")
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
        print("Open WebUI infrastructure")
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
        print(f"  Ollama base URL:   {desired['ollama_base_url']}")
        print(f"  experiment model:  {desired['model_default']}")
        print(f"  secret key file:   {desired['secret_key_file']}")

        backend = observed["backend"]
        print("  backend API:")
        print(f"    authenticated:   {backend['authenticated']}")
        print(f"    user:            {backend['email']}")
        print(f"    role:            {backend['role']}")
        print(f"    model available: {backend['model_available']}")
        if backend["model_ids"]:
            print("    models observed:  " + ", ".join(backend["model_ids"]))
        ollama_config = backend["ollama_config"]
        if ollama_config:
            print(
                "    Ollama persisted: "
                f"enabled={ollama_config.get('ENABLE_OLLAMA_API')} "
                f"base_urls={ollama_config.get('OLLAMA_BASE_URLS')}"
            )
        openai_config = backend["openai_config"]
        if openai_config:
            print(
                "    OpenAI persisted: "
                f"enabled={openai_config.get('ENABLE_OPENAI_API')}"
            )
        isolation_config = backend["isolation_config"]
        if isolation_config:
            matches = isolation_config == desired["experiment_isolation"]
            print(f"    isolation exact:  {matches}")
        for tool_id, tool_state in backend["managed_tools"].items():
            print(
                f"    tool {tool_id}: "
                f"present={tool_state['present']} exact={tool_state['exact']}"
            )
        if backend["error"]:
            print(f"    error:           {backend['error']}")
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
                "Refusing reset without --yes. The reset deletes mutable "
                "Open WebUI state.",
                file=sys.stderr,
            )
            return 2

        _, _, _, _, manager = self._context(args)
        removed = manager.reset(args.scope)
        if not removed:
            print(f"Open WebUI reset scope '{args.scope}' was already clean.")
            return 0

        print(f"Open WebUI reset scope '{args.scope}':")
        for path in removed:
            print(f"  removed {path}")
        return 0
