import json
from hermes_cli.setup import load_config
from hermes_cli.setup_quick import _blank_slate_minimal_toolsets

config = load_config()
_blank_slate_minimal_toolsets(config)
disabled = set(config.setdefault("agent", {}).get("disabled_toolsets") or [])
print(json.dumps(sorted(disabled)))
