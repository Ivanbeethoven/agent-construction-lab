"""Preflight and official-split membership tests with explicit metadata fixtures."""
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from agent_construction_lab.fixed_model.__main__ import doctor
from agent_construction_lab.fixed_model.environments import validate_appworld_membership
from agent_construction_lab.fixed_model.study import StudyConfig, StudyExecutionError, summarize


class ReadinessTests(unittest.TestCase):
    def test_official_split_cannot_be_forged_by_manifest(self):
        fake = types.ModuleType("appworld")
        splits = {"train": ["train1_1"], "dev": ["dev1_1"], "test_normal": ["test1_1"], "test_challenge": []}
        fake.load_task_ids = lambda name: splits[name]
        fake.update_root = lambda root: None
        with patch.dict("sys.modules", {"appworld": fake}):
            validate_appworld_membership({"tasks": [{"task_id": "train1_1", "benchmark_split": "train"}]}, "/tmp")
            with self.assertRaises(ValueError):
                validate_appworld_membership({"tasks": [{"task_id": "test1_1", "benchmark_split": "train"}]}, "/tmp")
            with self.assertRaises(ValueError):
                validate_appworld_membership({"tasks": [{"task_id": "unknown_1", "benchmark_split": "train"}]}, "/tmp")

    def test_placeholder_doctor_is_not_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.json"
            path.write_text(json.dumps({"model": "REPLACE_WITH_FIXED_MODEL", "base_url": "https://example.example/v1", "api_key_env": None}))
            report = doctor(str(path))
        self.assertFalse(report["ready_for_live_smoke"])
        self.assertFalse(report["live_model_probed"])

    def test_pairing_rejects_different_task_keys(self):
        def row(method, task):
            return {"condition": method, "domain": "same", "task_id": task, "construction_seed": 17,
                    "execution_seed": 101, "success": True, "tool_steps": 1, "failure_type": None,
                    "attempt_ids": [], "stage": "query"}
        rows = [row("F", "q1"), row("V", "q2")]
        with self.assertRaises(StudyExecutionError):
            summarize(rows, rows, [], [], StudyConfig(bootstrap_samples=10), mock=True)

    def test_explicit_same_search_space_limit(self):
        with self.assertRaises(ValueError):
            StudyConfig.from_dict({"max_modules": 4})


if __name__ == "__main__":
    unittest.main()
