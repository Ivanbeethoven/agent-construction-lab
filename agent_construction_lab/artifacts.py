"""Generate executable bounded harnesses and verify their dependency digests."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import types
from . import runtime
from .contracts import candidate_harnesses, TaskSpec
from ._io import digest, write_json


class ArtifactIntegrityError(ValueError):
    pass


@dataclass
class ArtifactInfo:
    path: str
    manifest_sha256: str


def compile_artifact(spec, mechanisms, out):
    spec = TaskSpec.from_dict(spec) if isinstance(spec, dict) else spec
    mechanisms = tuple(mechanisms)
    if mechanisms not in candidate_harnesses(spec):
        raise ValueError("Unsupported mechanisms")
    root = Path(out)
    root.mkdir(parents=True, exist_ok=True)
    lines = ['"""Reconstructed frozen simulator harness; no LLM evidence."""',
             "from agent_construction_lab import runtime"]
    names = []
    for mechanism in mechanisms:
        name = runtime.MIXINS[mechanism].__name__
        lines.extend([f"class {name}(runtime.{name}):", "    pass"])
        names.append(name)
    if "idempotency" in mechanisms:
        lines.append("# derive_idempotency_key is inherited from runtime.IdempotencyMixin")
    lines.extend([f"class FrozenHarness({', '.join(names + ['runtime.BaseHarness'])}):", "    pass",
                  f"MECHANISMS = {mechanisms!r}", "def run(spec, seed=0):",
                  "    return runtime.execute_harness(spec, MECHANISMS, seed, FrozenHarness)"])
    code = "\n".join(lines) + "\n"
    (root / "agent.py").write_text(code, encoding="utf-8", newline="\n")
    manifest = dict(schema_version=1, task=spec.to_dict(), mechanisms=list(mechanisms),
        construction_environment_rollouts=0, agent_sha256=hashlib.sha256(code.encode()).hexdigest(),
        runtime_sha256=hashlib.sha256(Path(runtime.__file__).read_bytes()).hexdigest())
    write_json(root / "manifest.json", manifest)
    write_json(root / "freeze.json", {"manifest_sha256": digest(manifest)})
    return ArtifactInfo(str(root), digest(manifest))


def verify_artifact(root, expected_manifest_hash=None):
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    expected = expected_manifest_hash or json.loads((root / "freeze.json").read_text())["manifest_sha256"]
    if digest(manifest) != expected:
        raise ArtifactIntegrityError("Manifest changed")
    if hashlib.sha256((root / "agent.py").read_bytes()).hexdigest() != manifest["agent_sha256"]:
        raise ArtifactIntegrityError("Agent changed")
    if hashlib.sha256(Path(runtime.__file__).read_bytes()).hexdigest() != manifest["runtime_sha256"]:
        raise ArtifactIntegrityError("Runtime changed")
    return manifest


def run_artifact(root, task=None, seed=0, expected_manifest_hash=None):
    manifest = verify_artifact(root, expected_manifest_hash)
    module = types.ModuleType("frozen_harness")
    exec(compile((Path(root) / "agent.py").read_text(encoding="utf-8"), "<verified-harness>", "exec"), module.__dict__)
    return module.run(task or TaskSpec.from_dict(manifest["task"]), seed)
