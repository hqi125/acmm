from __future__ import annotations

import json

from helpers import plugins
from helpers.tool import Response, Tool


_PLUGIN_NAME = "acmm_tools"


class GetMagicNumber(Tool):
    async def execute(self, **kwargs):
        del kwargs
        state = plugins.get_plugin_config(_PLUGIN_NAME, agent=self.agent) or {}
        mode = str(state.get("retrieval_mode", "success"))
        if mode != "success":
            return Response(
                message=json.dumps(
                    {"ok": False, "error": "controlled retrieval failure"}
                ),
                break_loop=False,
            )

        value = int(state.get("magic_number", 87))
        return Response(
            message=json.dumps({"ok": True, "magic_number": value}),
            break_loop=False,
        )
