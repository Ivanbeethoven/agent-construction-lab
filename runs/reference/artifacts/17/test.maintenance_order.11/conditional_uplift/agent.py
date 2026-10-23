"""Generated frozen harness. Actor is simulated; no LLM claim."""
from __future__ import annotations
from typing import Any, Mapping
from agent_construction_lab.runtime import BaseHarness, ToolError, execute_harness

class SchemaAdapterMixin:
    def prepare(self) -> None:
        super().prepare()
        self.schema = self.call("describe_schema", {})


class FrozenHarness(SchemaAdapterMixin, BaseHarness):
    pass

MECHANISMS = ('schema_adapter',)

def run(spec, seed=0):
    return execute_harness(spec, MECHANISMS, seed, FrozenHarness)
