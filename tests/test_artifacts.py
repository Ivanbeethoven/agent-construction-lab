import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_construction_lab.artifacts import ArtifactIntegrityError, compile_artifact, run_artifact, verify_artifact
from agent_construction_lab.fixtures import get_tasks
from agent_construction_lab.contracts import candidate_harnesses
from agent_construction_lab.runtime import run_episode


class ArtifactTests(unittest.TestCase):
    def test_construction_never_instantiates_world_and_executes_real_code(self):
        task = get_tasks("source")[2]
        with tempfile.TemporaryDirectory() as root:
            with patch("agent_construction_lab.runtime.SyntheticWorld.__init__", side_effect=AssertionError("construction touched environment")):
                info = compile_artifact(task.public_spec(), ("retry", "idempotency"), root)
            code = (Path(root) / "agent.py").read_text()
            self.assertIn("class RetryMixin", code)
            self.assertIn("derive_idempotency_key", code)
            self.assertEqual(verify_artifact(root, info.manifest_sha256)["construction_environment_rollouts"], 0)
            result = run_artifact(root, task, 17, info.manifest_sha256)
            self.assertTrue(result.success)
            self.assertEqual(result.to_dict(), run_episode(task, ("retry", "idempotency"), 17).to_dict())

    def test_mutation_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            task = get_tasks("source")[0]
            info = compile_artifact(task, (), root)
            (Path(root) / "agent.py").write_text("raise RuntimeError('tampered')\n")
            with self.assertRaises(ArtifactIntegrityError):
                run_artifact(root, task, 0, info.manifest_sha256)

    def test_no_op_and_max_two(self):
        with tempfile.TemporaryDirectory() as root:
            task = get_tasks("source")[0]
            compile_artifact(task, (), root)
            self.assertTrue(run_artifact(root, task).success)
            with self.assertRaises(ValueError):
                compile_artifact(task, ("retry", "checkpoint", "schema_adapter"), root)

    def test_compiled_behavior_matches_runtime_across_conditions(self):
        tasks = get_tasks("source")[:6]
        with tempfile.TemporaryDirectory() as root:
            for mechanisms in candidate_harnesses(tasks[0]):
                artifact = compile_artifact(tasks[0], mechanisms, root)
                for task in tasks:
                    for seed in (0, 5, 17):
                        with self.subTest(mechanisms=mechanisms, task=task.task_id, seed=seed):
                            compiled = run_artifact(root, task, seed, artifact.manifest_sha256)
                            direct = run_episode(task, mechanisms, seed)
                            self.assertEqual(compiled.to_dict(), direct.to_dict())


if __name__ == "__main__":
    unittest.main()
