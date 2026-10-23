"""Public simulator contracts reconstructed from archived specifications."""
from dataclasses import dataclass, field, asdict
from itertools import combinations
import copy

MECHANISMS = ("retry", "idempotency", "checkpoint", "schema_adapter")
FEATURES = {"persistent_storage", "restart_risk", "schema_discovery", "schema_variant", "structured_errors", "supports_idempotency", "transient_faults", "workload_size", "budget"}


@dataclass
class TaskSpec:
    task_id: str
    family: str
    split: str
    description: str
    budget: int
    features: dict

    def to_dict(self):
        return copy.deepcopy(asdict(self))

    @classmethod
    def from_dict(cls, value):
        return cls(**copy.deepcopy(value))

    def public_spec(self):
        return TaskSpec.from_dict(self.to_dict())


@dataclass
class RunResult:
    task_id: str
    family: str
    mechanisms: tuple
    execution_seed: int
    success: bool
    score: float
    cost_units: float
    tool_steps: int
    failure_type: str | None
    events: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def to_dict(self):
        result = asdict(self)
        result["mechanisms"] = list(self.mechanisms)
        return result

    @classmethod
    def from_dict(cls, value):
        data = {k: v for k, v in value.items() if k in cls.__dataclass_fields__}
        data["mechanisms"] = tuple(data["mechanisms"])
        return cls(**data)


def public_features(spec):
    if isinstance(spec, dict):
        spec = TaskSpec.from_dict(spec)
    return dict(spec.features, budget=spec.budget)


def candidate_harnesses(spec, max_mechanisms=2):
    if not 0 <= max_mechanisms <= 2:
        raise ValueError("At most two mechanisms are supported")
    features = public_features(spec)
    allowed = [m for m in MECHANISMS if (
        m != "idempotency" or features.get("supports_idempotency")) and (
        m != "checkpoint" or features.get("persistent_storage")) and (
        m != "schema_adapter" or features.get("schema_discovery"))]
    return [c for size in range(max_mechanisms + 1) for c in combinations(allowed, size)]
