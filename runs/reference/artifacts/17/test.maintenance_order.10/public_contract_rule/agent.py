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


class SchemaAdapterMixin:
    def prepare(self) -> None:
        super().prepare()
        self.schema = self.call("describe_schema", {})


class FrozenHarness(SchemaAdapterMixin, RetryMixin, BaseHarness):
    pass

MECHANISMS = ('retry', 'schema_adapter')

def run(spec, seed=0):
    return execute_harness(spec, MECHANISMS, seed, FrozenHarness)
