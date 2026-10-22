from __future__ import annotations

import json
import os
from typing import Any


GET_MAGIC_NUMBER = {
    "name": "get_magic_number",
    "description": (
        "Return the laboratory magic number for the current ACMM experiment."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


ADD_NUMBERS = {
    "name": "add_numbers",
    "description": "Add two numbers and return their sum.",
    "parameters": {
        "type": "object",
        "properties": {
            "a": {
                "type": "number",
                "description": "First number.",
            },
            "b": {
                "type": "number",
                "description": "Second number.",
            },
        },
        "required": ["a", "b"],
        "additionalProperties": False,
    },
}


def register(ctx: Any) -> None:
    ctx.register_tool(
        name="get_magic_number",
        toolset="acmm",
        schema=GET_MAGIC_NUMBER,
        handler=_get_magic_number,
    )

    ctx.register_tool(
        name="add_numbers",
        toolset="acmm",
        schema=ADD_NUMBERS,
        handler=_add_numbers,
    )


def _get_magic_number(params: dict[str, Any], **kwargs: Any) -> str:
    del params, kwargs

    if _controlled_mode("ACMM_RETRIEVAL_MODE") == "failure":
        raise RuntimeError("Controlled ACMM retrieval failure.")

    raw_value = os.getenv("ACMM_MAGIC_NUMBER")

    if raw_value is None:
        return json.dumps(
            {
                "error": "ACMM_MAGIC_NUMBER is not configured for this run."
            }
        )

    try:
        value = int(raw_value)
    except ValueError:
        return json.dumps(
            {
                "error": "ACMM_MAGIC_NUMBER is not a valid integer."
            }
        )

    return json.dumps(
        {
            "magic_number": value,
        }
    )


def _add_numbers(params: dict[str, Any], **kwargs: Any) -> str:
    del kwargs

    if _controlled_mode("ACMM_ADDITION_MODE") == "failure":
        raise RuntimeError("Controlled ACMM addition failure.")

    a = params.get("a")
    b = params.get("b")

    if not _is_number(a) or not _is_number(b):
        return json.dumps(
            {
                "error": "Both a and b must be numbers."
            }
        )

    return json.dumps(
        {
            "result": a + b,
        }
    )


def _controlled_mode(environment_name: str) -> str:
    value = os.getenv(environment_name, "success")

    if value not in {"success", "failure"}:
        raise RuntimeError(
            f"{environment_name} must be configured as 'success' or 'failure'."
        )

    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)
