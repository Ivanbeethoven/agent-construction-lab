"""Generated frozen harness. Actor is simulated; no LLM claim."""
from __future__ import annotations
from typing import Any, Mapping
from agent_construction_lab.runtime import BaseHarness, ToolError, execute_harness

class CheckpointMixin:
    def persist(self) -> None:
        super().persist()
        self.call("checkpoint_save", {"state": self.state})

    def restore(self) -> None:
        self.state = self.call("checkpoint_load", {})["state"]


class SchemaAdapterMixin:
    def prepare(self) -> None:
        super().prepare()
        self.schema = self.call("describe_schema", {})


class FrozenHarness(SchemaAdapterMixin, CheckpointMixin, BaseHarness):
    pass

MECHANISMS = ('checkpoint', 'schema_adapter')

def run(spec, seed=0):
    return execute_harness(spec, MECHANISMS, seed, FrozenHarness)
