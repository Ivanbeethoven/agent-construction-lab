import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_construction_lab.fixed_model import spec
from agent_construction_lab.fixed_model.constructors import make_harness
from agent_construction_lab.fixed_model.environments import (
    AppWorldEnvironment, BuiltinEnvironment, create_environment,
)


def manifest(benchmark="builtin"):
    domains = {name: {"profile": {"description": name + " public workflow"},
                       "features": {"workflow_depth": 1.0, "write_operations": 1.0}}
               for name in ("source-domain", "dev-domain", "target-domain")}
    tasks = [
        {"task_id": "src_1", "domain_id": "source-domain", "split": "source", "generator_id": "src"},
        {"task_id": "dev_1", "domain_id": "dev-domain", "split": "dev", "generator_id": "dev"},
        {"task_id": "tgt_1", "domain_id": "target-domain", "split": "support", "generator_id": "tgt"},
        {"task_id": "tgt_2", "domain_id": "target-domain", "split": "query", "generator_id": "tgt"},
    ]
    if benchmark == "appworld":
        for task in tasks:
            task["benchmark_split"] = "train" if task["split"] in {"source", "dev"} else "dev"
    return {"schema_version": 1, "benchmark": benchmark, "domains": domains, "tasks": tasks}


class ManifestIsolationTests(unittest.TestCase):
    def test_valid_partition_is_copied_and_keeps_support_query_in_one_domain(self):
        original = manifest()
        validated = spec.validate_manifest(original)
        self.assertEqual(validated, original)
        validated["domains"]["source-domain"]["features"]["workflow_depth"] = 100
        self.assertEqual(original["domains"]["source-domain"]["features"]["workflow_depth"], 1.0)

    def test_source_and_dev_domains_cannot_overlap_target_roles(self):
        for forbidden_domain in ("source-domain", "dev-domain"):
            value = manifest()
            value["tasks"].append({"task_id": "extra_1", "domain_id": forbidden_domain,
                                   "split": "support", "generator_id": "extra"})
            with self.assertRaises(ValueError):
                spec.validate_manifest(value)

    def test_generator_cannot_cross_roles_under_different_domain_labels(self):
        for generator in ("src", "dev"):
            value = manifest()
            value["tasks"][-1]["generator_id"] = generator
            with self.assertRaises(ValueError):
                spec.validate_manifest(value)

    def test_query_requires_support_and_task_ids_are_globally_unique(self):
        value = manifest()
        value["tasks"] = [task for task in value["tasks"] if task["split"] != "support"]
        with self.assertRaises(ValueError):
            spec.validate_manifest(value)
        value = manifest()
        value["tasks"].append(copy.deepcopy(value["tasks"][0]))
        with self.assertRaises(ValueError):
            spec.validate_manifest(value)

    def test_feature_schema_and_private_labels_are_checked(self):
        value = manifest()
        value["domains"]["target-domain"]["features"]["query_label"] = 1
        with self.assertRaises(ValueError):
            spec.validate_manifest(value)
        value = manifest()
        del value["domains"]["target-domain"]["features"]["workflow_depth"]
        with self.assertRaises(ValueError):
            spec.validate_manifest(value)

    def test_appworld_official_test_is_never_source_dev_or_support(self):
        spec.validate_manifest(manifest("appworld"))
        for role in ("source", "dev", "support"):
            for official_split in ("test_normal", "test_challenge"):
                value = manifest("appworld")
                next(task for task in value["tasks"] if task["split"] == role)["benchmark_split"] = official_split
                with self.assertRaises(ValueError, msg=f"{official_split} must not supply {role} feedback"):
                    spec.validate_manifest(value)
        value = manifest("appworld")
        value["tasks"][0]["benchmark_split"] = "dev"
        with self.assertRaises(ValueError):
            spec.validate_manifest(value)

    def test_appworld_generator_must_be_present_and_match_task_id(self):
        value = manifest("appworld")
        del value["tasks"][0]["generator_id"]
        with self.assertRaises(ValueError):
            spec.validate_manifest(value)
        value = manifest("appworld")
        value["tasks"][0]["generator_id"] = "invented-generator"
        with self.assertRaises(ValueError):
            spec.validate_manifest(value)


class FrozenHarnessTests(unittest.TestCase):
    def test_frozen_harness_and_runtime_are_verified_against_external_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            for name in ("executor.py", "client.py", "environments.py", "spec.py", "appworld_process.py", "environment_worker.py", "code_policy.py"):
                (runtime / name).write_text("# isolated dependency fixture\n", encoding="utf-8")
            harness = make_harness(["plan", "verify"])
            artifact = root / "artifact"
            with patch.object(spec, "__file__", str(runtime / "spec.py")):
                frozen = spec.freeze_harness(harness, artifact, {"condition": "F", "domain_id": "held-out"})
                self.assertEqual(spec.load_frozen_harness(artifact, expected_manifest_hash=frozen["manifest_sha256"]), harness)
                original_harness = (artifact / "harness.json").read_bytes()
                (artifact / "harness.json").write_text(json.dumps(make_harness(())), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "harness changed"):
                    spec.load_frozen_harness(artifact, expected_manifest_hash=frozen["manifest_sha256"])
                (artifact / "harness.json").write_bytes(original_harness)
                (runtime / "executor.py").write_text("# changed executor\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Runtime dependency changed"):
                    spec.load_frozen_harness(artifact, expected_manifest_hash=frozen["manifest_sha256"])

    def test_manifest_tampering_and_unexpected_dependency_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory)
            frozen = spec.freeze_harness(make_harness(()), artifact, {"condition": "G"})
            path = artifact / "manifest.json"
            original = json.loads(path.read_text(encoding="utf-8"))
            changed = copy.deepcopy(original)
            changed["metadata"]["condition"] = "F"
            path.write_text(spec.canonical_json(changed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "manifest changed"):
                spec.load_frozen_harness(artifact, expected_manifest_hash=frozen["manifest_sha256"])
            changed = copy.deepcopy(original)
            changed["runtime_files"] = {"../external.py": "not-a-real-digest"}
            path.write_text(spec.canonical_json(changed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unexpected runtime dependency"):
                spec.load_frozen_harness(artifact, expected_manifest_hash=spec.digest(changed))


class BuiltinEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.task = {"task_id": "fixture_1", "payload": {"item_id": "sku-1", "initial_quantity": 2, "target_quantity": 7}}

    def test_terminal_score_depends_on_real_state_and_explicit_completion(self):
        env = create_environment("builtin", self.task)
        self.assertFalse(env.evaluate()["success"])
        observed = json.loads(env.execute('print(apis.inventory.get_item(item_id="sku-1"))'))
        self.assertEqual(observed[0]["quantity"], 2)
        env.execute('apis.inventory.set_quantity(item_id="sku-1", quantity=7)')
        self.assertFalse(env.evaluate()["success"])
        env.execute("apis.supervisor.complete_task()")
        self.assertEqual(env.evaluate()["score"], 1.0)
        self.assertTrue(env.evaluate()["details"]["fixture_only"])
        env.execute('apis.inventory.set_quantity(item_id="sku-1", quantity=8)')
        self.assertFalse(env.evaluate()["success"])
        env.close()

    def test_new_episode_has_fresh_state_and_closed_world_rejects_execution(self):
        first = BuiltinEnvironment(self.task)
        first.execute('apis.inventory.set_quantity(item_id="sku-1", quantity=7)\napis.supervisor.complete_task()')
        self.assertTrue(first.evaluate()["success"])
        first.close()
        with self.assertRaises(RuntimeError):
            first.execute('apis.inventory.get_item(item_id="sku-1")')
        second = BuiltinEnvironment(self.task)
        self.assertEqual(second.quantity, 2)
        self.assertEqual(second.writes, 0)
        self.assertFalse(second.completed)
        self.assertFalse(second.evaluate()["success"])
        second.close()

    def test_illegal_syntax_is_validated_before_any_statement_executes(self):
        illegal_suffixes = ("import os", "value = 1", "for i in range(2): pass", "__import__('os').system('true')")
        for suffix in illegal_suffixes:
            env = BuiltinEnvironment(self.task)
            output = json.loads(env.execute('apis.inventory.set_quantity(item_id="sku-1", quantity=7)\n' + suffix))
            self.assertIn("error", output)
            self.assertEqual(env.quantity, 2)
            self.assertEqual(env.writes, 0)
            env.close()

    def test_arbitrary_file_access_and_nonliteral_arguments_are_never_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "must_not_exist.txt"
            env = BuiltinEnvironment(self.task)
            malicious = f"__import__('pathlib').Path({str(marker)!r}).write_text('bad')"
            self.assertIn("error", json.loads(env.execute(malicious)))
            self.assertFalse(marker.exists())
            self.assertIn("error", json.loads(env.execute('apis.inventory.set_quantity(item_id="sku-1", quantity=int("7"))')))
            self.assertEqual(env.quantity, 2)
            self.assertEqual(env.writes, 0)
            env.close()

    def test_legal_api_runtime_error_does_not_pretend_to_roll_back_prior_effects(self):
        env = BuiltinEnvironment(self.task)
        output = json.loads(env.execute('apis.inventory.set_quantity(item_id="sku-1", quantity=7)\n'
                                        'apis.inventory.get_item(item_id="unknown")'))
        self.assertIn("error", output)
        self.assertEqual(env.quantity, 7)
        self.assertEqual(env.writes, 1)
        self.assertFalse(env.completed)
        env.close()

    def test_appworld_version_guard_runs_before_loading_a_world(self):
        with patch("agent_construction_lab.fixed_model.environments.version", return_value="0.0.0"):
            with self.assertRaisesRegex(RuntimeError, "Expected appworld"):
                AppWorldEnvironment({"task_id": "example_1"}, appworld_root="/tmp/not_used", experiment_name="test", seed=1)


if __name__ == "__main__":
    unittest.main()
