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


class SchemaAdapterMixin:
    def prepare(self) -> None:
        super().prepare()
        self.schema = self.call("describe_schema", {})


class FrozenHarness(SchemaAdapterMixin, IdempotencyMixin, BaseHarness):
    pass

MECHANISMS = ('idempotency', 'schema_adapter')

def run(spec, seed=0):
    return execute_harness(spec, MECHANISMS, seed, FrozenHarness)
