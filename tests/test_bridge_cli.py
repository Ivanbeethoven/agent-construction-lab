import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from agent_construction_lab.bridge import RunnerConfig, RunnerError, SubprocessRunner
from agent_construction_lab.cli import main
from agent_construction_lab.fixtures import get_tasks
from agent_construction_lab.runtime import run_episode


class BridgeTests(unittest.TestCase):
    def test_worker_roundtrip_matches_fresh_direct_episode(self):
        task = get_tasks("source")[2]
        runner = SubprocessRunner(RunnerConfig((sys.executable, "-m", "agent_construction_lab.worker")))
        result = runner(task, ("retry", "idempotency"), seed=19)
        direct = run_episode(task, ("retry", "idempotency"), seed=19)
        self.assertEqual((result.success, result.score, result.events), (direct.success, direct.score, direct.events))
        self.assertEqual(runner.invocations, 1)
        self.assertEqual(result.metrics["external_usage"]["model_calls"], 0)

    def test_mismatched_worker_response_is_rejected(self):
        script = 'import json; print(json.dumps({"schema_version": 1, "result": {"task_id": "wrong", "family": "wrong"}}))'
        runner = SubprocessRunner(RunnerConfig((sys.executable, "-c", script)))
        with self.assertRaisesRegex(RunnerError, "different task"):
            runner(get_tasks("source")[0])
        self.assertEqual(runner.failures, 1)

    def test_shell_string_is_not_accepted(self):
        with self.assertRaises(ValueError):
            RunnerConfig.from_dict({"argv": "python worker.py; arbitrary_shell"})

    def test_cli_build_then_verify_and_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task = root / "task.json"
            task.write_text(json.dumps(get_tasks("test")[2].to_dict()))
            artifact = root / "frozen"
            commands = [
                ["build", "--task", str(task), "--mechanisms", "retry", "idempotency", "--out", str(artifact)],
                ["verify", "--artifact", str(artifact)],
                ["run", "--artifact", str(artifact), "--seed", "99", "--out", str(root / "outcome.json")],
            ]
            for command in commands:
                done = subprocess.run([sys.executable, "-m", "agent_construction_lab", *command], capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stderr)
            outcome = json.loads((root / "outcome.json").read_text())
            self.assertTrue(outcome["success"])
            self.assertEqual(json.loads((artifact / "manifest.json").read_text())["construction_environment_rollouts"], 0)


if __name__ == "__main__":
    unittest.main()
