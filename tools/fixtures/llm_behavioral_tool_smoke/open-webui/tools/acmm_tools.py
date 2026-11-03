"""
title: ACMM Tools
author: ACMM
version: 0.1.0
required_open_webui_version: 0.11.4
description: Controlled tools used by ACMM LLM behavioral qualification.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        magic_number: int | None = Field(
            default=None,
            description="Controlled magic number for the active ACMM run.",
        )
        retrieval_mode: str = Field(
            default="success",
            description="Controlled retrieval mode: success or failure.",
        )
        addition_mode: str = Field(
            default="success",
            description="Controlled addition mode: success or failure.",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    async def get_magic_number(self) -> str:
        """Return the laboratory magic number for the current ACMM experiment."""
        _validate_mode("retrieval_mode", self.valves.retrieval_mode)

        if self.valves.retrieval_mode == "failure":
            raise RuntimeError("Controlled ACMM retrieval failure.")

        if self.valves.magic_number is None:
            return json.dumps(
                {
                    "error": "ACMM magic number is not configured for this run.",
                }
            )

        return json.dumps(
            {
                "magic_number": self.valves.magic_number,
            }
        )

    async def add_numbers(self, a: float, b: float) -> str:
        """
        Add two numbers and return their sum.

        :param a: First number.
        :param b: Second number.
        """
        _validate_mode("addition_mode", self.valves.addition_mode)

        if self.valves.addition_mode == "failure":
            raise RuntimeError("Controlled ACMM addition failure.")

        result = a + b
        if isinstance(result, float) and result.is_integer():
            result = int(result)

        return json.dumps(
            {
                "result": result,
            }
        )


def _validate_mode(name: str, value: str) -> None:
    if value not in {"success", "failure"}:
        raise RuntimeError(f"{name} must be configured as 'success' or 'failure'.")
