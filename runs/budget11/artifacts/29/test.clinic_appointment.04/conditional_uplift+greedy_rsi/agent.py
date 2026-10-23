"""Generated frozen harness. Actor is simulated; no LLM claim."""
from __future__ import annotations
from typing import Any, Mapping
from agent_construction_lab.runtime import BaseHarness, ToolError, execute_harness

class IdempotencyMixin:
    def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        args = dict(arguments)
        if name == "commit_record":
            self.world.local_operation("derive_idempotency_key")
            args["idempotency_key"] = "record:" + str(args["id"])
        return super().call(name, args)


class FrozenHarness(IdempotencyMixin, BaseHarness):
    pass

MECHANISMS = ('idempotency',)

def run(spec, seed=0):
    return execute_harness(spec, MECHANISMS, seed, FrozenHarness)
