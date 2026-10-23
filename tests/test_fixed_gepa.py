"""Protocol tests plus integration with the actual optional GEPA wheel."""
import copy
from importlib import metadata
import json
import unittest
from unittest import mock

from agent_construction_lab.fixed_model import gepa_adapter as adapter


SEED = {"schema_version": 1, "system_prompt": "Solve the task.", "modules": []}
IMPROVED = {"schema_version": 1, "system_prompt": "Solve and verify the task.", "modules": ["verify"]}


def official_gepa_installed():
    try:
        return metadata.version("gepa") == adapter.GEPA_VERSION
    except metadata.PackageNotFoundError:
        return False


class DependencyAndContractTests(unittest.TestCase):
    def test_missing_dependency_is_actionable_and_never_falls_back(self):
        evaluator, reflector = mock.Mock(), mock.Mock()
        with mock.patch.object(adapter.metadata, "version", side_effect=metadata.PackageNotFoundError("gepa")):
            with self.assertRaisesRegex(adapter.MissingGepaDependencyError, "gepa==0.1.4"):
                adapter.optimize_harness(SEED, evaluator, reflector, max_evaluations=4, seed=0)
        evaluator.assert_not_called()
        reflector.assert_not_called()

    def test_different_version_requires_explicit_reaudit(self):
        with mock.patch.object(adapter.metadata, "version", return_value="99.0.0"):
            with self.assertRaises(adapter.UnsupportedGepaVersionError):
                adapter.optimize_harness(SEED, mock.Mock(), mock.Mock(), max_evaluations=4, seed=0)

    def test_invalid_seed_never_reaches_an_evaluator(self):
        cases = [
            dict(SEED, schema_version=True),
            dict(SEED, modules=["plan", "verify", "tool_docs"]),
            dict(SEED, modules=["verify", "verify"]),
            dict(SEED, modules=["arbitrary_python"]),
            dict(SEED, model="a-different-model"),
        ]
        for candidate in cases:
            with self.subTest(candidate=candidate):
                evaluator = mock.Mock()
                with self.assertRaises(ValueError):
                    adapter.optimize_harness(candidate, evaluator, mock.Mock(), max_evaluations=4, seed=0)
                evaluator.assert_not_called()


@unittest.skipUnless(official_gepa_installed(), "Install gepa==0.1.4 to run official optimizer integration tests")
class OfficialGepaIntegrationTests(unittest.TestCase):
    def test_real_optimizer_improves_and_charges_all_four_executions(self):
        executions, messages_seen = [], []

        def evaluate(harness):
            executions.append(copy.deepcopy(harness))
            return (1.0 if "verify" in harness["modules"] else 0.0), {"needed": "verify"}

        def reflect(messages):
            messages_seen.append(messages)
            return "```json\n" + json.dumps(IMPROVED) + "\n```"

        result = adapter.optimize_harness(SEED, evaluate, reflect, max_evaluations=4, seed=17)
        self.assertEqual(result["selected_harness"], IMPROVED)
        self.assertEqual(result["selected_score"], 1.0)
        self.assertEqual(executions, [SEED, SEED, IMPROVED, IMPROVED])
        self.assertEqual(result["audit"]["actual_evaluations"], 4)
        self.assertEqual(result["audit"]["gepa_metric_calls"], 4)
        self.assertEqual(result["audit"]["unused_evaluation_budget"], 0)
        self.assertFalse(result["audit"]["cache_evaluation"])
        self.assertEqual(len(messages_seen), 1)
        self.assertEqual(messages_seen[0][0]["role"], "user")
        self.assertIn("needed", messages_seen[0][0]["content"])
        json.dumps(result, allow_nan=False)

    def test_repeated_identical_candidates_are_not_hidden_by_cache(self):
        calls = []

        def evaluate(harness):
            calls.append(harness)
            return 0.25, {"feedback": "No observed improvement"}

        result = adapter.optimize_harness(
            SEED, evaluate, lambda messages: json.dumps(SEED), max_evaluations=6, seed=0
        )
        # Two rejected rounds consume parent+proposal each, in addition to seed.
        self.assertEqual(len(calls), 5)
        self.assertEqual(result["audit"]["actual_evaluations"], 5)
        self.assertEqual(result["audit"]["reflection_calls"], 2)
        self.assertEqual(result["audit"]["unused_evaluation_budget"], 1)

    def test_small_budgets_count_seed_and_never_start_unfunded_round(self):
        for budget in (1, 2, 3):
            with self.subTest(budget=budget):
                evaluator = mock.Mock(return_value=(0.0, {}))
                reflector = mock.Mock(return_value=json.dumps(IMPROVED))
                result = adapter.optimize_harness(SEED, evaluator, reflector, max_evaluations=budget, seed=0)
                self.assertEqual(result["audit"]["actual_evaluations"], 1)
                self.assertEqual(result["audit"]["unused_evaluation_budget"], budget - 1)
                self.assertEqual(evaluator.call_count, 1)
                reflector.assert_not_called()

    def test_invalid_proposal_is_charged_without_executing_invalid_harness(self):
        responses = iter(["not JSON", json.dumps(IMPROVED)])
        executions = []

        def evaluate(harness):
            executions.append(harness)
            return float("verify" in harness["modules"]), {"needed": "verify"}

        result = adapter.optimize_harness(
            SEED, evaluate, lambda messages: next(responses), max_evaluations=6, seed=0
        )
        self.assertEqual(result["selected_harness"], IMPROVED)
        self.assertEqual(result["audit"]["candidate_evaluation_requests"], 6)
        self.assertEqual(result["audit"]["invalid_candidate_evaluations"], 1)
        self.assertEqual(result["audit"]["actual_evaluations"], len(executions))
        self.assertEqual(len(executions), 5)
        json.dumps(result, allow_nan=False)

    def test_model_error_propagates_without_alternative_model(self):
        reflector = mock.Mock(side_effect=ConnectionError("fixed model unavailable"))
        with self.assertRaisesRegex(ConnectionError, "fixed model unavailable"):
            adapter.optimize_harness(SEED, lambda h: (0.0, {}), reflector, max_evaluations=4, seed=0)
        self.assertEqual(reflector.call_count, 1)

    def test_nonfinite_support_score_fails_loudly(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            adapter.optimize_harness(
                SEED, lambda h: (float("nan"), {}), lambda messages: json.dumps(IMPROVED),
                max_evaluations=4, seed=0,
            )


if __name__ == "__main__":
    unittest.main()
