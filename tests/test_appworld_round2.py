"""Independent round-two selection: fixed IDs, opaque query data, no provider."""
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_construction_lab.fixed_model.spec import digest, validate_manifest
from scripts.prepare_appworld_round2 import (
    PRIOR_CONTACT_GENERATORS, build_round2, default_study_config, main,
)
from scripts.prepare_appworld_pilot import write_outputs


class RoundTwoSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="acl-round2-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        datasets = self.root / "data" / "datasets"
        datasets.mkdir(parents=True)
        splits = {"train": ("train_a", "train_b", "train_c", "train_d", "82e2fac", "afc0fce"),
                  "dev": ("dev_a", "dev_b", "dev_c", "6c2c621", "4ec8de5")}
        for split, generators in splits.items():
            ids = [f"{g}_{i}" for g in generators for i in range(1, 4)]
            (datasets / f"{split}.txt").write_text("\n".join(ids), encoding="utf-8")
            for task_id in ids:
                task = self.root / "data" / "tasks" / task_id
                task.mkdir(parents=True)
                (task / "specs.json").write_text(json.dumps({"instruction": "PRIVATE_" + task_id}), encoding="utf-8")

    def test_complete_design_budget_and_public_projection(self):
        public, local, report = build_round2(self.root)
        self.assertEqual(validate_manifest(local), local)
        self.assertEqual(report["tasks_by_role"], {"source": 6, "support": 3, "query": 3})
        self.assertEqual(report["planned_episode_upper_bounds"], {"source": 66, "dev": 0, "support": 24, "query": 21, "total": 111})
        self.assertEqual(report["planned_requests"]["main_actor_upper_bound"], 2664)
        self.assertEqual(report["planned_requests"]["nominal_total_including_calibration_and_preflight"], 2693)
        self.assertEqual(report["declared_round_limits"]["max_model_calls_including_external"], 2800)
        selected = {t["generator_id"] for t in public["tasks"]}
        self.assertEqual(len(selected), 4)
        self.assertFalse(selected & set(PRIOR_CONTACT_GENERATORS))
        self.assertNotIn(public["calibration"]["generator_id"], selected)
        self.assertEqual(len(public["calibration"]["task_ids"]), 3)
        self.assertEqual(len(local["domains"]["source.personal_apps"]["profile"]["public_examples"]), 6)
        self.assertEqual(len(local["domains"]["target.personal_apps"]["profile"]["public_examples"]), 3)
        self.assertNotIn("PRIVATE_", json.dumps(public))
        self.assertNotIn("PRIVATE_", json.dumps(report))
        for task in local["tasks"]:
            self.assertEqual("PRIVATE_" + task["task_id"] in json.dumps(local), task["split"] in {"source", "support"})
        self.assertEqual(local["public_manifest_sha256"], digest(public))
        self.assertEqual(report["local_manifest_sha256"], digest(local))

    def test_never_decodes_query_calibration_unused_or_official_test_content(self):
        public, _, _ = build_round2(self.root)
        allowed = {t["task_id"] for t in public["tasks"] if t["split"] in {"source", "support"}}
        original = Path.read_text
        def guard(path, *args, **kwargs):
            if "ground_truth" in path.parts or path.name in {"test_normal.txt", "test_challenge.txt"}:
                raise AssertionError("Private or official test data read")
            if path.name == "specs.json" and path.parent.name not in allowed:
                raise AssertionError("Held-out/calibration instruction decoded")
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", guard):
            again, _, _ = build_round2(self.root)
        self.assertEqual(public, again)

    def test_opaque_query_and_calibration_bytes_do_not_change_assignment_or_profiles(self):
        public, local, _ = build_round2(self.root)
        opaque = public["calibration"]["task_ids"] + [t["task_id"] for t in public["tasks"] if t["split"] == "query"]
        for task_id in opaque:
            (self.root / "data" / "tasks" / task_id / "specs.json").write_bytes(b"not JSON: must never be decoded")
        changed, changed_local, _ = build_round2(self.root)
        self.assertEqual([t["task_id"] for t in public["tasks"]], [t["task_id"] for t in changed["tasks"]])
        self.assertEqual(local["domains"], changed_local["domains"])
        self.assertEqual(public["selection"], changed["selection"])
        self.assertNotEqual(digest(public), digest(changed))

    def test_missing_selected_variant_fails_instead_of_replacing_generator(self):
        public, _, _ = build_round2(self.root)
        selected = next(t for t in public["tasks"] if t["split"] == "query")
        path = self.root / "data" / "datasets" / "dev.txt"
        path.write_text("\n".join(x for x in path.read_text(encoding="utf-8").splitlines() if x != selected["task_id"]), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "no replacements"):
            build_round2(self.root)

    def test_source_instruction_changes_cannot_drive_selection(self):
        public, _, _ = build_round2(self.root)
        for t in public["tasks"]:
            if t["split"] in {"source", "support"}:
                (self.root / "data" / "tasks" / t["task_id"] / "specs.json").write_text('{"instruction":"Different public task"}', encoding="utf-8")
        changed, _, _ = build_round2(self.root)
        self.assertEqual([t["task_id"] for t in public["tasks"]], [t["task_id"] for t in changed["tasks"]])
        self.assertEqual(public["calibration"], changed["calibration"])

    def test_registered_axes_and_limits_reject_silent_design_change(self):
        config = default_study_config()
        for changes in ({"max_steps": 12}, {"max_modules": 1}, {"support_seeds": None},
                        {"execution_seeds": (101, 102)}, {"methods": ("G", "F")}, {"rsi_budgets": (8,)}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "registered"):
                build_round2(self.root, replace(config, **changes))

    def test_missing_source_instruction_fails_without_replacement(self):
        public, _, _ = build_round2(self.root)
        task_id = next(t["task_id"] for t in public["tasks"] if t["split"] == "source")
        (self.root / "data" / "tasks" / task_id / "specs.json").write_text('{"instruction":""}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "lacks"):
            build_round2(self.root)

    def test_no_overwrite_and_local_only_private_outputs(self):
        public, local, report = build_round2(self.root)
        outputs = (self.root / "manifests" / "round2.json", self.root / "runs" / "local-live" / "round2.local.json", self.root / "runs" / "local-live" / "report.local.json")
        write_outputs(public, local, report, *outputs)
        before = [p.read_bytes() for p in outputs]
        with self.assertRaises(FileExistsError):
            write_outputs(public, local, report, *outputs)
        self.assertEqual(before, [p.read_bytes() for p in outputs])
        with self.assertRaisesRegex(ValueError, "Local outputs"):
            write_outputs(public, local, report, self.root / "public2.json", self.root / "private.json", outputs[2])

    def test_dry_run_has_no_file_or_model_side_effects(self):
        paths = [self.root / "new-public.json", self.root / "runs" / "local-live" / "new.local.json", self.root / "runs" / "local-live" / "new-report.local.json"]
        with patch("sys.stdout", new_callable=io.StringIO) as stdout, patch("socket.create_connection", side_effect=AssertionError("No model/network call")):
            main(["--appworld-root", str(self.root), "--public-out", str(paths[0]), "--local-out", str(paths[1]), "--report-out", str(paths[2]), "--dry-run"])
        result = json.loads(stdout.getvalue())
        self.assertTrue(result["dry_run"])
        self.assertFalse(result["real_model_called"])
        self.assertNotIn("PRIVATE_", stdout.getvalue())
        self.assertFalse(any(p.exists() for p in paths))


if __name__ == "__main__":
    unittest.main()
