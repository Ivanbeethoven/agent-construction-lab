"""Additional research-integrity boundaries beyond the recovered original tests.

All HTTP results here come from an explicit local fixture; these checks are
engineering evidence, not measured scientific performance.
"""
import copy
from dataclasses import replace
from importlib import metadata
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_construction_lab.fixed_model import mock_server
from agent_construction_lab.fixed_model.environments import create_environment
from agent_construction_lab.fixed_model.client import ModelConfig
from agent_construction_lab.fixed_model.constructors import (
    ConstructorConfig, ConstructorDataError, ConstructorLibrary,
    ConstructorResponseError, construct_domain, make_harness,
)
from agent_construction_lab.fixed_model.gepa_adapter import optimize_harness
from agent_construction_lab.fixed_model.spec import validate_manifest
from agent_construction_lab.fixed_model.study import StudyConfig, StudyExecutionError, run_study
from tests.test_fixed_constructors import source_records


def _official_gepa():
    try:
        return metadata.version("gepa") == "0.1.4"
    except metadata.PackageNotFoundError:
        return False


def _lines(path):
    return [json.loads(row) for row in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _small_config(**changes):
    values = dict(methods=("G",), max_modules=0, max_steps=4, bootstrap_samples=10)
    values.update(changes)
    return StudyConfig(**values)


def _appworld_manifest():
    return {
        "schema_version": 1, "benchmark": "appworld",
        "domains": {name: {"profile": {"description": "Public API task"}, "features": {"complexity": 1.0}}
                    for name in ("source", "target")},
        "tasks": [
            {"task_id": "src_0", "domain_id": "source", "split": "source", "generator_id": "src", "benchmark_split": "train"},
            {"task_id": "dst_0", "domain_id": "target", "split": "support", "generator_id": "dst", "benchmark_split": "dev"},
            {"task_id": "dst_1", "domain_id": "target", "split": "query", "generator_id": "dst", "benchmark_split": "dev"},
        ],
    }


class ConfigurationGuardTests(unittest.TestCase):
    def test_invalid_study_parameters_fail_before_any_execution(self):
        invalid = (
            {"source_seeds": (True,)}, {"execution_seeds": (-1,)}, {"construction_seeds": ("17",)},
            {"support_seeds": ()}, {"support_seeds": (303, 303)}, {"support_seeds": (-1,)},
            {"support_seeds": (True,)}, {"require_complete_token_usage": 1},
            {"rsi_budgets": (1.5,)}, {"max_steps": True}, {"max_context_chars": 0},
            {"bootstrap_samples": 1.5}, {"ridge_alpha": float("nan")}, {"cost_weight": float("inf")},
            {"source_summary_max_chars": 0}, {"support_feedback_max_chars": -1},
            {"appworld_timeout_seconds": float("nan")}, {"appworld_initialization_timeout_seconds": 0},
            {"methods": ("G", "G")}, {"ablations": ("no_conditions", "no_conditions")},
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    StudyConfig(**values)

    def test_nonfinite_numerical_constructor_parameters_are_rejected(self):
        for values in ({"ridge_alpha": float("nan")}, {"ridge_alpha": float("inf")},
                       {"cost_weight": float("nan")}, {"cost_weight": float("inf")}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ConstructorConfig(**values)

    def test_direct_live_api_requires_snapshot_and_response_identity_before_environment(self):
        config = ModelConfig(model="fixed-live-snapshot", model_snapshot="fixed-live-snapshot",
                             expected_response_model="fixed-live-snapshot", api_key_env=None)
        variants = (replace(config, model_snapshot=None), replace(config, expected_response_model=None),
                    replace(config, model_snapshot="REPLACE_WITH_MODEL_SNAPSHOT"),
                    replace(config, expected_response_model="REPLACE_WITH_RESPONSE_MODEL"))
        with tempfile.TemporaryDirectory() as directory:
            for index, model in enumerate(variants):
                with self.subTest(model=model), \
                        patch("agent_construction_lab.fixed_model.study.validate_appworld_membership") as membership, \
                        patch("agent_construction_lab.fixed_model.study.create_environment") as environment, \
                        patch("agent_construction_lab.fixed_model.client.OpenAICompatibleClient.complete") as complete:
                    with self.assertRaises((ValueError, StudyExecutionError)):
                        run_study(_appworld_manifest(), model, Path(directory) / str(index), config=_small_config(), mock=False)
                    environment.assert_not_called()
                    complete.assert_not_called()

    def test_builtin_fixture_cannot_be_mislabeled_as_live_research(self):
        with tempfile.TemporaryDirectory() as directory, mock_server.local_mock_model() as model:
            with patch("agent_construction_lab.fixed_model.client.OpenAICompatibleClient.complete") as complete:
                with self.assertRaises((ValueError, StudyExecutionError)):
                    run_study(mock_server.mock_manifest(), model, Path(directory) / "run", config=_small_config(), mock=False)
                complete.assert_not_called()

    def test_strict_support_query_group_isolation_is_explicit_and_enforced(self):
        manifest = _appworld_manifest()
        # Legacy instance-transfer studies can still share a task generator.
        self.assertEqual(validate_manifest(manifest)["tasks"], manifest["tasks"])
        manifest["group_disjoint_support_query"] = False
        validate_manifest(manifest)
        for invalid_flag in (None, 0, 1, "true", "false"):
            with self.subTest(flag=invalid_flag), self.assertRaises(ValueError):
                validate_manifest(dict(manifest, group_disjoint_support_query=invalid_flag))
        manifest["group_disjoint_support_query"] = True
        with self.assertRaisesRegex(ValueError, "disjoint support/query"):
            validate_manifest(manifest)
        isolated = copy.deepcopy(manifest)
        isolated["tasks"][-1].update(task_id="heldout_0", generator_id="heldout")
        self.assertTrue(validate_manifest(isolated)["group_disjoint_support_query"])


class SourceDataGuardTests(unittest.TestCase):
    def test_explicitly_invalid_source_outcomes_never_become_zero_labels(self):
        for fields in ({"valid_task_outcome": False}, {"infrastructure_error": True}):
            records = source_records()
            records[1].update(fields)
            with self.subTest(fields=fields), self.assertRaises(ConstructorDataError):
                ConstructorLibrary.from_source(records)

    def test_duplicate_intervention_cannot_silently_reweight_source_evidence(self):
        records = source_records()
        records.append(copy.deepcopy(records[1]))
        with self.assertRaises(ConstructorDataError):
            ConstructorLibrary.from_source(records)

    def test_direct_builder_obeys_the_registered_module_limit(self):
        records = [row for row in source_records() if len(row["modules"]) <= 1]
        library = ConstructorLibrary.from_source(records, ConstructorConfig(max_modules=1))
        with self.assertRaises(ConstructorResponseError):
            construct_domain("D", domain_id="target", public_profile={"description": "Public API"},
                             features={"risk": 0.0, "complexity": 0.0}, library=library,
                             model_call=lambda prompt, role: json.dumps(make_harness(["plan", "verify"])))


class StudyAuditGuardTests(unittest.TestCase):
    @unittest.skipUnless(_official_gepa(), "Requires pinned official GEPA")
    def test_support_and_query_use_their_own_registered_execution_seeds(self):
        config = _small_config(methods=("G+R",), rsi_budgets=(1,),
                               support_seeds=(303,), execution_seeds=(101, 102))
        with tempfile.TemporaryDirectory() as directory, mock_server.local_mock_model() as model:
            root = Path(directory) / "run"
            run_study(mock_server.mock_manifest(), model, root, config=config, mock=True)
            episodes = _lines(root / "episodes.jsonl")
            usage = _lines(root / "model_usage.jsonl")
            support = [row for row in episodes if row["stage"] == "support"]
            query = [row for row in episodes if row["stage"] == "query"]
            self.assertEqual(len(support), 2)  # One support task per target domain, one seed.
            self.assertEqual(len(query), 8)  # Two target domains, two query tasks, two seeds.
            self.assertEqual({row["execution_seed"] for row in support}, {303})
            self.assertEqual({row["execution_seed"] for row in query}, {101, 102})
            self.assertEqual({row["request_seed"] for row in usage if row["phase"].startswith("support:")}, {303})
            self.assertEqual({row["request_seed"] for row in usage if row["phase"].startswith("query:")}, {101, 102})
            stored = json.loads((root / "study_config.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["support_seeds"], [303])
            self.assertEqual(stored["execution_seeds"], [101, 102])

    def test_strict_actor_usage_rejects_provider_total_without_input_output(self):
        original = mock_server._response

        def total_only_usage(body):
            response = original(body)
            response["usage"] = {"total_tokens": response["usage"]["total_tokens"]}
            return response

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            with patch.object(mock_server, "_response", side_effect=total_only_usage), mock_server.local_mock_model() as model:
                with self.assertRaises(StudyExecutionError):
                    run_study(mock_server.mock_manifest(), model, root, config=_small_config(), mock=True)
            self.assertEqual(_lines(root / "source_pairs.jsonl"), [])
            self.assertEqual(_lines(root / "query_results.jsonl"), [])
            self.assertFalse((root / "summary.json").exists())

    def _assert_unknown_nonactor_usage_aborts(self, methods):
        original = mock_server._response

        def omit_builder_usage(body):
            response = original(body)
            if not body.get("tools") and len(body["messages"]) == 1:
                response.pop("usage", None)
            return response

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            with patch.object(mock_server, "_response", side_effect=omit_builder_usage), mock_server.local_mock_model() as model:
                with self.assertRaises(StudyExecutionError):
                    run_study(mock_server.mock_manifest(), model, root,
                              config=_small_config(methods=methods, max_modules=2), mock=True)
            status = json.loads((root / "run_status.json").read_text(encoding="utf-8"))
            self.assertFalse(status["completed"])
            self.assertIn("usage", status["error"].lower())
            self.assertTrue(any(row["total_tokens"] is None for row in _lines(root / "model_usage.jsonl")))
            self.assertEqual(_lines(root / "query_results.jsonl"), [])
            self.assertFalse((root / "summary.json").exists())

    def test_strict_usage_covers_direct_builder(self):
        self._assert_unknown_nonactor_usage_aborts(("D",))

    @unittest.skipUnless(_official_gepa(), "Requires pinned official GEPA")
    def test_strict_usage_covers_reflection(self):
        self._assert_unknown_nonactor_usage_aborts(("G+R",))

    def test_mutated_frozen_harness_is_rejected_before_first_query(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"

            def mutate_at_query_barrier(event):
                if event["stage"] == "query":
                    lock = json.loads((root / "query_lock.json").read_text(encoding="utf-8"))
                    path = root / lock["plans"][0]["path"] / "harness.json"
                    harness = json.loads(path.read_text(encoding="utf-8"))
                    harness["system_prompt"] += " Unauthorized post-freeze change."
                    path.write_text(json.dumps(harness), encoding="utf-8")

            with mock_server.local_mock_model() as model:
                with self.assertRaises(StudyExecutionError):
                    run_study(mock_server.mock_manifest(), model, root, config=_small_config(),
                              mock=True, progress=mutate_at_query_barrier)
            self.assertFalse(any(row["phase"].startswith("query:") for row in _lines(root / "model_usage.jsonl")))
            self.assertEqual(_lines(root / "query_results.jsonl"), [])

    def test_separate_runs_have_disjoint_backend_experiment_names(self):
        names = [set(), set()]
        current = 0

        def create_and_record(*args, **kwargs):
            names[current].add(kwargs["experiment_name"])
            return create_environment(*args, **kwargs)

        with tempfile.TemporaryDirectory() as directory, mock_server.local_mock_model() as model:
            with patch("agent_construction_lab.fixed_model.study.create_environment", side_effect=create_and_record):
                for current in range(2):
                    run_study(mock_server.mock_manifest(), model, Path(directory) / str(current), config=_small_config(), mock=True)
        self.assertTrue(names[0])
        self.assertTrue(names[1])
        self.assertEqual(names[0] & names[1], set(), "Different runs must not overwrite AppWorld's experiment output/state paths")


@unittest.skipUnless(_official_gepa(), "Requires pinned official GEPA")
class OptimizerBudgetGuardTests(unittest.TestCase):
    def test_selected_score_is_the_official_validation_score_not_parent_remeasurement(self):
        values = iter([0.8, 0.1, 0.0])
        result = optimize_harness(make_harness(), lambda harness: (next(values), {}),
                                  lambda messages: json.dumps(make_harness(["verify"])), max_evaluations=4)
        self.assertEqual(result["selected_harness"], make_harness())
        self.assertEqual(result["selected_score"], 0.8)
        self.assertEqual(result["audit"]["actual_evaluations"], 3)

    def test_optimizer_rejects_overbudget_seed_before_support_execution(self):
        with patch("builtins.print"):
            with self.assertRaises(ValueError):
                optimize_harness(make_harness(["plan", "verify"]),
                                 lambda harness: self.fail("Invalid seed reached support"),
                                 lambda messages: self.fail("Invalid seed reached reflection"),
                                 max_evaluations=4, max_modules=1)

    def test_optimizer_never_executes_overbudget_proposal(self):
        executed = []

        def evaluate(harness):
            self.assertLessEqual(len(harness["modules"]), 1)
            executed.append(copy.deepcopy(harness))
            return 0.25, {"feedback": "Bounded support observation"}

        result = optimize_harness(make_harness(), evaluate,
                                  lambda messages: json.dumps(make_harness(["plan", "verify"])),
                                  max_evaluations=4, max_modules=1)
        self.assertTrue(executed)
        self.assertLessEqual(len(result["selected_harness"]["modules"]), 1)
        self.assertEqual(result["audit"]["invalid_candidate_evaluations"], 1)
        self.assertLessEqual(result["audit"]["candidate_evaluation_requests"], 4)


if __name__ == "__main__":
    unittest.main()
