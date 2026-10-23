"""JSON subprocess bridge; shell execution is deliberately unsupported."""
from dataclasses import dataclass
import json
import subprocess
from .contracts import RunResult


class RunnerError(RuntimeError):
    pass


@dataclass
class RunnerConfig:
    argv: tuple
    timeout: float = 30.0

    def __post_init__(self):
        if isinstance(self.argv, str) or not self.argv:
            raise ValueError("argv must be a nonempty argument sequence")

    @classmethod
    def from_dict(cls, value):
        return cls(**value)


class SubprocessRunner:
    def __init__(self, config):
        self.config = config
        self.invocations = self.failures = 0

    def __call__(self, spec, mechanisms=(), seed=0):
        self.invocations += 1
        try:
            completed = subprocess.run(self.config.argv, input=json.dumps(dict(schema_version=1, task=spec.to_dict(),
                mechanisms=list(mechanisms), seed=seed)), capture_output=True, text=True, encoding="utf-8",
                timeout=self.config.timeout, check=True)
            payload = json.loads(completed.stdout)
            result = payload["result"]
            if result["task_id"] != spec.task_id or result["family"] != spec.family:
                raise RunnerError("Worker returned a different task")
            parsed = RunResult.from_dict(result)
            parsed.metrics["external_usage"] = payload.get("usage", {"model_calls": None})
            return parsed
        except Exception as error:
            self.failures += 1
            if isinstance(error, RunnerError):
                raise
            raise RunnerError(f"Worker failed: {type(error).__name__}") from error
