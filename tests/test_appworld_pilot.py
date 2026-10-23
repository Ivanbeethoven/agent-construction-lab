"""Task selection must not inspect held-out instructions or publish profiles."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_construction_lab.fixed_model.spec import digest, validate_manifest
from agent_construction_lab.fixed_model.study import StudyConfig
from scripts.prepare_appworld_pilot import build_pilot, budget_report, write_outputs


class AppWorldPilotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="acl-pilot-")
        self.root = Path(self.temporary.name)
        (self.root / "data" / "datasets").mkdir(parents=True)
        for split, generators in (("train", ("train_a", "train_b")), ("dev", ("dev_a", "dev_b"))):
            ids = [f"{generator}_{index}" for generator in generators for index in range(1, 4)]
            (self.root / "data" / "datasets" / f"{split}.txt").write_text("\n".join(ids), encoding="utf-8")
            for task_id in ids:
                path = self.root / "data" / "tasks" / task_id
                path.mkdir(parents=True)
                (path / "specs.json").write_text(json.dumps({"instruction": f"PRIVATE_TASK_TEXT_{task_id}"}), encoding="utf-8")
        self.config = StudyConfig(source_seeds=(101,), support_seeds=(101,), execution_seeds=(101,),
                                  construction_seeds=(17,), rsi_budgets=(4,))

    def tearDown(self):
        self.temporary.cleanup()

    def test_generator_partition_budget_and_local_public_separation(self):
        public, local, report = build_pilot(self.root, self.config)
        self.assertEqual(validate_manifest(local), local)
        self.assertTrue(public["group_disjoint_support_query"])
        self.assertEqual(report["planned_episode_upper_bounds"],
                         {"source": 33, "dev": 0, "support": 24, "query": 21, "total": 78})
        self.assertEqual(report["tasks_by_role"], {"source": 3, "support": 3, "query": 3})
        self.assertEqual(len(set(report["selected_generators"].values())), 3)
        self.assertNotIn("PRIVATE_TASK_TEXT", json.dumps(public))
        self.assertNotIn("PRIVATE_TASK_TEXT", json.dumps(report))
        for task in local["tasks"]:
            marker = f"PRIVATE_TASK_TEXT_{task['task_id']}"
            self.assertEqual(marker in json.dumps(local), task["split"] in {"source", "support"})
        self.assertEqual(local["public_manifest_sha256"], digest(public))
        self.assertEqual(report["local_manifest_sha256"], digest(local))

    def test_no_query_unused_or_official_test_content_read_for_preparation(self):
        public, _, _ = build_pilot(self.root, self.config)
        allowed_ids = {task["task_id"] for task in public["tasks"] if task["split"] in {"source", "support"}}
        original = Path.read_text

        def guarded(path, *args, **kwargs):
            if path.name in {"test_normal.txt", "test_challenge.txt"} or "ground_truth" in path.parts:
                raise AssertionError("Official test/ground truth was opened")
            if path.name == "specs.json" and path.parent.name not in allowed_ids:
                raise AssertionError("Held-out instruction was decoded")
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", guarded):
            second, _, _ = build_pilot(self.root, self.config)
        self.assertEqual(public, second)

    def test_query_content_cannot_change_selection_or_target_profile(self):
        public, local, _ = build_pilot(self.root, self.config)
        query = next(task for task in public["tasks"] if task["split"] == "query")
        path = self.root / "data" / "tasks" / query["task_id"] / "specs.json"
        # Opaque hashing must even allow non-JSON bytes in held-out specs. The
        # actual environment, not preparation, validates task execution later.
        path.write_bytes(b"query instruction should not be parsed as JSON")
        changed, local_changed, _ = build_pilot(self.root, self.config)
        self.assertEqual(public["selection"], changed["selection"])
        self.assertEqual(local["domains"], local_changed["domains"])
        self.assertEqual([task["task_id"] for task in public["tasks"]], [task["task_id"] for task in changed["tasks"]])
        self.assertNotEqual(digest(public), digest(changed))

    def test_missing_variant_fails_without_replacement(self):
        path = self.root / "data" / "datasets" / "dev.txt"
        path.write_text("\n".join(path.read_text(encoding="utf-8").splitlines()[:-1]), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "three variants"):
            build_pilot(self.root, self.config)

    def test_calibration_generator_is_excluded_before_selection(self):
        _, _, first = build_pilot(self.root, self.config)
        excluded = first["selected_generators"]["source"]
        public, _, second = build_pilot(self.root, self.config, excluded_generators=[excluded])
        self.assertNotEqual(second["selected_generators"]["source"], excluded)
        self.assertFalse(any(task["generator_id"] == excluded for task in public["tasks"]))
        self.assertIn("82e2fac", public["selection"]["excluded_calibration_generators"])

    def test_rejects_cross_split_generator_and_unsafe_id(self):
        path = self.root / "data" / "datasets" / "dev.txt"
        path.write_text("../outside_1", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            build_pilot(self.root, self.config)
        path.write_text("train_a_4", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "share a generator"):
            build_pilot(self.root, self.config)

    def test_support_seed_fallback_changes_budget(self):
        public, _, _ = build_pilot(self.root, self.config)
        config = StudyConfig(execution_seeds=(101, 102), support_seeds=None)
        report = budget_report(public, config)
        self.assertEqual(report["planned_episode_upper_bounds"]["support"], 48)
        self.assertEqual(report["planned_episode_upper_bounds"]["query"], 42)

    def test_private_output_must_be_local_and_no_overwrite(self):
        public, local, report = build_pilot(self.root, self.config)
        public_out = self.root / "manifests" / "pilot.json"
        local_out = self.root / "runs" / "local-live" / "pilot.local.json"
        report_out = self.root / "runs" / "local-live" / "report.local.json"
        with self.assertRaisesRegex(ValueError, "Local outputs"):
            write_outputs(public, local, report, public_out, self.root / "leak.json", report_out)
        write_outputs(public, local, report, public_out, local_out, report_out)
        with self.assertRaises(FileExistsError):
            write_outputs(public, local, report, public_out, local_out, report_out)
        self.assertNotIn("PRIVATE_TASK_TEXT", public_out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
