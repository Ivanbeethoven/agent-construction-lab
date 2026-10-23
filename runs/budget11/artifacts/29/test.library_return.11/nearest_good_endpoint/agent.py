"""Generated frozen harness. Actor is simulated; no LLM claim."""
from __future__ import annotations
from typing import Any, Mapping
from agent_construction_lab.runtime import BaseHarness, ToolError, execute_harness

class RetryMixin:
    def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        for attempt in range(3):
            try:
                return super().call(name, arguments)
            except ToolError as error:
                if not error.retryable or attempt == 2:
                    raise
                self.world.local_operation("retry_decision")
                self.world._event("retry", action=name, attempt=attempt + 1)
        raise AssertionError("unreachable")


class IdempotencyMixin:
    def call(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        args = dict(arguments)
        if name == "commit_record":
            self.world.local_operation("derive_idempotency_key")
            args["idempotency_key"] = "record:" + str(args["id"])
        return super().call(name, args)


class FrozenHarness(IdempotencyMixin, RetryMixin, BaseHarness):
    pass

MECHANISMS = ('retry', 'idempotency')

def run(spec, seed=0):
    return execute_harness(spec, MECHANISMS, seed, FrozenHarness)
