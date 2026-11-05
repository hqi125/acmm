from hermes_cli.setup import load_config, save_config
from hermes_cli.setup_quick import (
    _blank_slate_minimal_toolsets,
    _blank_slate_minimize_config,
)

keep = set(__KEEP__)
config = load_config()
_blank_slate_minimal_toolsets(config)
_blank_slate_minimize_config(config)
disabled = set(config.setdefault("agent", {}).get("disabled_toolsets") or [])
config["agent"]["disabled_toolsets"] = sorted(disabled)
save_config(config)
actual_keep = set(config.setdefault("platform_toolsets", {}).get("cli") or [])
if actual_keep != keep:
    raise RuntimeError(
        "Pinned Blank Slate helper produced unexpected CLI toolsets: "
        + repr(sorted(actual_keep))
    )
print("Blank Slate policy applied: " + ",".join(sorted(actual_keep)))
