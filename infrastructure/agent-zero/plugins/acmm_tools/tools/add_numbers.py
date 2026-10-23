from __future__ import annotations

import json

from helpers import plugins
from helpers.tool import Response, Tool


_PLUGIN_NAME = "acmm_tools"


class AddNumbers(Tool):
    async def execute(self, **kwargs):
        args = {**self.args, **kwargs}
        state = plugins.get_plugin_config(_PLUGIN_NAME, agent=self.agent) or {}
        mode = str(state.get("addition_mode", "success"))
        if mode != "success":
            return Response(
                message=json.dumps(
                    {"ok": False, "error": "controlled addition failure"}
                ),
                break_loop=False,
            )

        a = _number(args.get("a"))
        b = _number(args.get("b"))
        return Response(
            message=json.dumps({"ok": True, "result": a + b}),
            break_loop=False,
        )


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("a and b must be numbers")
    return value
