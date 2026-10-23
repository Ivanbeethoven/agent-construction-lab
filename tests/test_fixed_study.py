"""End-to-end pipeline validation with an explicitly scripted HTTP provider.

The mock intentionally favors plan/verify. Its success rates are NOT benchmark
results or evidence for a construction method. These tests exercise the actual
HTTP client, executable environments, frozen artifacts, cost ledger, and the
official pinned GEPA package; no handwritten score fixture replaces that path.
"""

from collections import Counter, defaultdict
from dataclasses import replace
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from agent_construction_lab.fixed_model.mock_server import MOCK_MODEL, local_mock_model, mock_manifest
from agent_construction_lab.fixed_model.spec import digest, load_frozen_harness
from agent_construction_lab.fixed_model.study import StudyConfig, StudyExecutionError, run_study


CONDITIONS = {"G", "D", "S", "V", "F", "G+R@4", "F+R@4"}


def small_config(**changes):
    fields = dict(source_seeds=(101,), execution_seeds=(101,), construction_seeds=(17,),
                  rsi_budgets=(4,), max_steps=4, bootstrap_samples=100)
    fields.update(changes)
    return StudyConfig(**fields)


def json_file(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def json_lines(path):
    path = Path(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


class FixedStudyPipelineTests(unittest.TestCase):
    """Run all seven conditions once; assertions audit the recorded run."""

    @classmethod
    def setUpClass(cls):
        try:
            installed = version("gepa")
        except PackageNotFoundError:
            raise unittest.SkipTest("Optional official gepa==0.1.4 is absent; run this suite in the validated study environment")
        if installed != "0.1.4":
            raise AssertionError(f"Installed GEPA version {installed!r} is not the validated 0.1.4")
        cls.temporary = tempfile.TemporaryDirectory(prefix="acl-fixed-study-test-")
        cls.root = Path(cls.temporary.name) / "run"
        cls.progress_events = []
        cls.query_barrier = None

        def progress(event):
            cls.progress_events.append(event)
            if event["stage"] == "query":
                # Executed before the first query request, not inferred after
                # the study ends from a self-reported boolean alone.
                lock = json_file(cls.root / "query_lock.json")
                builds = json_lines(cls.root / "constructions.jsonl")
                verified = []
                for plan in lock["plans"]:
                    loaded = load_frozen_harness(cls.root / plan["path"], expected_manifest_hash=plan["manifest_sha256"])
                    verified.append(digest(loaded))
                cls.query_barrier = {"lock": lock, "builds": builds, "verified": verified,
                    "episodes": json_lines(cls.root / "episodes.jsonl"),
                    "usage": json_lines(cls.root / "model_usage.jsonl")}

        try:
            with local_mock_model() as model:
                cls.summary = run_study(mock_manifest(), model, cls.root, config=small_config(), mock=True, progress=progress)
            cls.episodes = json_lines(cls.root / "episodes.jsonl")
            cls.pairs = json_lines(cls.root / "source_pairs.jsonl")
            cls.builds = json_lines(cls.root / "constructions.jsonl")
            cls.queries = json_lines(cls.root / "query_results.jsonl")
            cls.usage = json_lines(cls.root / "model_usage.jsonl")
            cls.lock = json_file(cls.root / "query_lock.json")
        except BaseException:
            cls.temporary.cleanup()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_all_seven_conditions_run_and_results_remain_marked_mock(self):
        self.assertEqual(set(self.summary["methods"]), CONDITIONS)
        self.assertEqual({row["condition"] for row in self.queries}, CONDITIONS)
        self.assertEqual({row["condition"] for row in self.lock["plans"]}, CONDITIONS)
        self.assertEqual(len(self.queries), 7 * 2 * 2)  # conditions × target domains × query tasks
        self.assertEqual(self.summary["frozen_harnesses"], 7 * 2)
        self.assertTrue(self.summary["mock_model"])
        self.assertIn("not research evidence", self.summary["validation_scope"])
        self.assertEqual(self.summary["dependency_versions"]["gepa"], "0.1.4")
        self.assertTrue(json_file(self.root / "run_status.json")["completed"])
        searches = json_lines(self.root / "support_searches.jsonl")
        self.assertEqual(len(searches), 2 * 2)  # G+R/F+R × domains
        self.assertTrue(any(row["role"] == "optimizer" for row in self.usage))

    def test_source_has_22_physical_episodes_and_no_op_reuses_baseline(self):
        physical = [row for row in self.episodes if row["stage"] == "source"]
        self.assertEqual(len(physical), 22)
        self.assertEqual(len({row["episode_id"] for row in physical}), 22)
        self.assertEqual(Counter(row["condition"] for row in physical),
                         {"source_baseline": 2, "source_intervention": 20})
        self.assertEqual(self.summary["actual_episode_counts"]["source"], 22)
        self.assertEqual(len(self.pairs), 22)
        self.assertEqual(self.summary["source_pairs"], 22)
        grouped = defaultdict(list)
        for row in self.pairs:
            grouped[row["task_id"], row["seed"]].append(row)
            self.assertEqual(row["split"], "source")
            self.assertEqual(row["role"], "source")
            self.assertEqual(row["seed"], row["baseline_seed"])
        for rows in grouped.values():
            self.assertEqual(len(rows), 11)
            self.assertEqual(len({row["baseline_episode_id"] for row in rows}), 1)
            no_op = [row for row in rows if not row["modules"]]
            self.assertEqual(len(no_op), 1)
            self.assertEqual(no_op[0]["episode_id"], no_op[0]["baseline_episode_id"])
            self.assertEqual(no_op[0]["success"], no_op[0]["baseline_success"])
            self.assertEqual(no_op[0]["cost"], no_op[0]["baseline_cost"])
            self.assertEqual(len({row["episode_id"] for row in rows}), 11)

    def test_one_frozen_harness_per_domain_condition_is_reused_across_queries(self):
        plans = {(row["domain"], row["condition"], row["construction_seed"]): row for row in self.lock["plans"]}
        groups = defaultdict(list)
        for row in self.queries:
            groups[row["domain"], row["condition"], row["construction_seed"]].append(row)
        self.assertEqual(set(groups), set(plans))
        for key, rows in groups.items():
            self.assertEqual(len({row["task_id"] for row in rows}), 2)
            self.assertEqual(len({row["harness_sha256"] for row in rows}), 1)
            plan = plans[key]
            harness = load_frozen_harness(self.root / plan["path"], expected_manifest_hash=plan["manifest_sha256"])
            self.assertEqual({row["harness_sha256"] for row in rows}, {digest(harness)})
            self.assertTrue(all(row["harness"] == harness for row in rows))

    def test_query_starts_after_all_support_and_construction_have_finished(self):
        barrier = self.query_barrier
        self.assertIsNotNone(barrier)
        self.assertEqual(len(barrier["builds"]), 14)
        self.assertEqual(len(barrier["verified"]), 14)
        self.assertTrue(barrier["lock"]["all_constructions_frozen_before_query"])
        self.assertTrue(barrier["episodes"])
        self.assertTrue(any(row["stage"] == "support" for row in barrier["episodes"]))
        self.assertFalse(any(row["stage"] == "query" for row in barrier["episodes"]))
        self.assertFalse(any(row["phase"].startswith("query:") for row in barrier["usage"]))
        stages = [row["stage"] for row in self.episodes]
        first_query = stages.index("query")
        self.assertTrue(all(stage == "query" for stage in stages[first_query:]))
        self.assertTrue(all(index < first_query for index, stage in enumerate(stages) if stage == "support"))
        phases = [row["phase"] for row in self.usage]
        first_query_attempt = next(index for index, phase in enumerate(phases) if phase.startswith("query:"))
        self.assertTrue(all(phase.startswith("query:") for phase in phases[first_query_attempt:]))

    def test_attempt_ownership_and_token_totals_cover_every_physical_call_once(self):
        ledger_ids = [row["attempt_id"] for row in self.usage]
        self.assertEqual(len(set(ledger_ids)), len(ledger_ids))
        owners = Counter(attempt for row in self.episodes + self.builds for attempt in row["attempt_ids"])
        self.assertEqual(set(owners), set(ledger_ids))
        self.assertTrue(all(count == 1 for count in owners.values()), "A physical attempt must have exactly one episode/build owner")
        self.assertEqual({row["request_model"] for row in self.usage}, {MOCK_MODEL})
        self.assertEqual({row["response_model"] for row in self.usage}, {MOCK_MODEL})
        self.assertEqual({row["expected_response_model"] for row in self.usage}, {MOCK_MODEL})
        self.assertTrue(all(row["success"] for row in self.usage))
        total = self.summary["total_actual_usage"]
        self.assertEqual(total["model_calls"], len(self.usage))
        for field in ("input_tokens", "output_tokens", "total_tokens"):
            self.assertEqual(total[field], sum(row[field] for row in self.usage))
            self.assertEqual(total["unknown_" + field + "_attempts"], 0)
        self.assertEqual(total["total_tokens"], total["input_tokens"] + total["output_tokens"])
        self.assertIsNone(total["cost_usd"], "Unprovided prices must not become zero dollars")
        ledger = {row["attempt_id"]: row for row in self.usage}
        source_ids = {attempt for row in self.episodes if row["stage"] == "source" for attempt in row["attempt_ids"]}
        source_tokens = sum(ledger[attempt]["total_tokens"] for attempt in source_ids)
        self.assertEqual(self.summary["source_usage"]["total_tokens"], source_tokens)
        for condition, metrics in self.summary["methods"].items():
            ids = {attempt for row in self.episodes if row["stage"] != "source" and row["condition"] == condition for attempt in row["attempt_ids"]}
            build_conditions = {condition}
            if "+R@" in condition:
                build_conditions.add(condition.split("+R@")[0])
            ids.update(attempt for row in self.builds if row["condition"] in build_conditions for attempt in row["attempt_ids"])
            measured = sum(ledger[attempt]["total_tokens"] for attempt in ids)
            self.assertEqual(metrics["measured_method_usage"]["total_tokens"], measured)
            allocated = source_tokens if metrics["source_cost_included"] else 0
            self.assertEqual(metrics["cold_total_tokens_including_source"], measured + allocated)
        self.assertIn("do not sum", self.summary["accounting_note"])

    def test_run_artifacts_verify_after_copying_to_a_new_directory(self):
        relocated = Path(self.temporary.name) / "relocated" / "run"
        shutil.copytree(self.root, relocated)
        lock = json_file(relocated / "query_lock.json")
        for entry in lock["plans"] + json_lines(relocated / "constructions.jsonl"):
            relative = Path(entry["path"])
            self.assertFalse(relative.is_absolute())
            self.assertNotIn("..", relative.parts)
            loaded = load_frozen_harness(relocated / relative, expected_manifest_hash=entry["manifest_sha256"])
            self.assertEqual(loaded["schema_version"], 1)
            self.assertLessEqual(len(loaded["modules"]), 2)


class FixedStudyInfrastructureFailureTests(unittest.TestCase):
    """This check runs even without GEPA; G-only still audits source failures."""

    def test_wrong_model_aborts_and_preserves_usage_without_training_labels(self):
        with tempfile.TemporaryDirectory(prefix="acl-fixed-study-failed-") as directory:
            root = Path(directory) / "run"
            with local_mock_model() as model:
                mismatched = replace(model, expected_response_model="different-required-fixed-snapshot")
                with self.assertRaises(StudyExecutionError):
                    run_study(mock_manifest(), mismatched, root, config=small_config(methods=("G",)), mock=True)
            status = json_file(root / "run_status.json")
            self.assertFalse(status["completed"])
            self.assertEqual(status["stage"], "failed")
            self.assertEqual(status["usage"]["failed_attempts"], 1)
            self.assertEqual(status["usage"]["model_calls"], 1)
            attempts = json_lines(root / "model_usage.jsonl")
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0]["error_type"], "response_model_identity_mismatch")
            self.assertEqual(attempts[0]["response_model"], MOCK_MODEL)
            self.assertIsNotNone(attempts[0]["total_tokens"])
            episodes = json_lines(root / "episodes.jsonl")
            self.assertEqual(len(episodes), 1)
            self.assertEqual(episodes[0]["stage"], "source")
            self.assertEqual(episodes[0]["failure_category"], "infrastructure")
            self.assertFalse(episodes[0]["valid_task_outcome"])
            self.assertEqual(json_lines(root / "source_pairs.jsonl"), [])
            self.assertEqual(json_lines(root / "query_results.jsonl"), [])
            self.assertFalse((root / "constructor_library.json").exists())
            self.assertFalse((root / "summary.json").exists())
            self.assertIn("no failure label", status["error"])


if __name__ == "__main__":
    unittest.main()
