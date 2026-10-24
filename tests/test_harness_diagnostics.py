"""Synthetic diagnosis checks: no provider calls, private data, or live evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_construction_lab._io import digest, write_json
from agent_construction_lab.fixed_model.constructors import ConstructorConfig, ConstructorLibrary, candidate_module_sets, construct_domain, make_harness
from agent_construction_lab.fixed_model.study import StudyConfig
from scripts import diagnose_harness_collapse as diagnosis


SECRET = "NEVER_PUBLISH_SIMULATED_ACCOUNT_OR_TASK_TEXT_7A23"


class HarnessDiagnosisTests(unittest.TestCase):
    def setUp(self):
        guard = patch("socket.create_connection", side_effect=AssertionError("Diagnosis cannot contact any provider"))
        guard.start()
        self.addCleanup(guard.stop)
        self.config = StudyConfig()
        self.manifest = dict(schema_version=1, benchmark="appworld", domains={
            name: dict(profile=dict(description=SECRET), features=dict(stateful=1)) for name in ("source", "target")},
            tasks=[dict(task_id=f"src_{i}", generator_id="src", domain_id="source", split="source", benchmark_split="train")
                for i in range(3)] + [dict(task_id=f"sup_{i}", generator_id="sup", domain_id="target", split="support", benchmark_split="dev")
                for i in range(3)] + [dict(task_id="qry_1", generator_id="qry", domain_id="target", split="query", benchmark_split="dev")])
        records = [dict(task_id=f"src_{task}", domain_id="source", split="source", role="source", seed=101, baseline_seed=101,
            features=dict(stateful=1), modules=list(modules), baseline_success=False, success=False,
            baseline_score=0.0, score=0.0, baseline_cost=10, cost=10 + len(modules))
            for task in range(3) for modules in candidate_module_sets()]
        self.library = ConstructorLibrary.from_source(records, ConstructorConfig(2.0, 0.0, 2))
        self.episodes = [dict(stage="source", valid_task_outcome=True, success=False, score=0.0,
            failure_type="step_budget_exhausted", trace=SECRET, answer=SECRET) for _ in records]
        self.harnesses, self.builds, self.searches = {}, [], []
        for condition in ("G", "F", "S", "V"):
            built = construct_domain(condition, domain_id="target", public_profile=self.manifest["domains"]["target"]["profile"],
                features=dict(stateful=1), library=self.library, model_call=lambda *args: '{"candidate_id":"c00"}')
            self.harnesses[condition] = built["harness"]
            self.builds.append(dict(path=condition, condition=condition, domain="target", construction_seed=17,
                harness_sha256=digest(built["harness"]), metadata=built["metadata"], attempt_ids=["selector-1"] if condition == "S" else []))
        for condition in ("G+R@4", "F+R@4"):
            self.harnesses[condition] = make_harness()
            self.builds.append(dict(path=condition, condition=condition, domain="target", construction_seed=17,
                harness_sha256=digest(make_harness()), metadata={}, attempt_ids=[]))
            self.searches.append(dict(condition=condition, domain="target", construction_seed=17, selected_harness=make_harness(), selected_score=1.0,
                audit=dict(actual_evaluations=3, candidate_evaluation_requests=3, invalid_candidate_evaluations=0,
                    reflection_calls=1, cache_evaluation=False, gepa_metric_calls=3, unused_evaluation_budget=1)))
            for harness in (make_harness(), make_harness(), make_harness(["plan"], SECRET)):
                self.episodes.extend(dict(stage="support", condition=condition, domain="target", construction_seed=17,
                    task_id=f"sup_{task}", execution_seed=101, harness=harness, harness_sha256=digest(harness),
                    score=1.0, success=True, valid_task_outcome=True, failure_type=None, usage=dict(total_tokens=20),
                    trace=SECRET, answer=SECRET) for task in range(3))
        self.episodes.append(dict(stage="query", score=SECRET, trace=SECRET))
        self.ledger = [dict(attempt_id="selector-1", phase="construction:S", success=True, response_text=SECRET)]

    def diagnose(self):
        return diagnosis.diagnose_data(self.manifest, self.config, self.library.to_dict(), self.episodes,
                                       self.builds, self.searches, self.harnesses, self.ledger)

    def test_zero_labels_produce_eleven_tied_candidates_and_empty_first_choice(self):
        report = self.diagnose()
        self.assertEqual(report["source"]["episodes"], 33)
        self.assertEqual(report["source"]["paired_effect_distribution"], {"0.0": 33})
        self.assertEqual(report["source"]["basis_numerical_rank"], 11)
        for row in report["numeric_constructors"]:
            self.assertEqual(row["nonzero_score_coefficients"], 0)
            self.assertEqual(len(row["tied_maximum_candidates"]), 11)
            self.assertEqual(row["selected_candidate_id"], "c00")
            self.assertFalse(row["token_prediction_influences_objective"])

    def test_signal_changes_predictions_without_forcing_methods_to_disagree(self):
        records = copy.deepcopy(self.library.records)
        for row in records:
            row["baseline_score"] = 0.2
            row["score"] = 0.8 if "verify" in row["modules"] else 0.2
        library = ConstructorLibrary.from_source(records, self.library.config)
        for condition in ("F", "V"):
            built = construct_domain(condition, domain_id="target", public_profile=dict(description="Public"),
                                     features=dict(stateful=1), library=library)
            result = diagnosis.numeric_diagnosis(library, "target", dict(profile={}, features=dict(stateful=1)),
                                                condition, built["metadata"], built["harness"])
            self.assertGreater(result["nonzero_score_coefficients"], 0)
            self.assertNotEqual(result["selected_candidate_id"], "c00")

    def test_distinct_proposal_at_support_ceiling_retains_seed(self):
        for search in self.diagnose()["feedback_optimization"]:
            self.assertEqual([row["objective"] for row in search["callbacks"]], [1, 1, 1])
            self.assertEqual(search["unique_evaluated_harnesses"], 2)
            self.assertFalse(search["proposal_accepted"])
            self.assertEqual(search["callbacks"][-1]["inferred_role"], "reflected_proposal")
            self.assertEqual(search["audit"]["unused_evaluation_budget"], 1)

    def test_incomplete_or_reordered_callback_is_rejected(self):
        support = [row for row in self.episodes if row["stage"] == "support"]
        for rows in (support[1:], [support[1], support[0]] + support[2:]):
            with self.assertRaisesRegex(ValueError, "callback"):
                diagnosis.support_callbacks(rows, self.manifest, self.config, self.searches[0])

    def test_invalid_support_label_and_mutated_prediction_are_rejected(self):
        next(row for row in self.episodes if row["stage"] == "support")["valid_task_outcome"] = False
        with self.assertRaisesRegex(ValueError, "Invalid support"):
            self.diagnose()
        next(row for row in self.episodes if row["stage"] == "support")["valid_task_outcome"] = True
        next(row for row in self.builds if row["condition"] == "F")["metadata"]["predictions"][0]["objective"] = 0.1
        with self.assertRaisesRegex(ValueError, "prediction mismatch"):
            self.diagnose()

    def test_selector_requires_real_recorded_call_and_reports_no_unobserved_reason(self):
        selector = self.diagnose()["selector"][0]
        self.assertEqual(selector["selected_candidate_id"], "c00")
        self.assertFalse(selector["model_rationale_observed"])
        self.ledger[0]["success"] = False
        with self.assertRaisesRegex(ValueError, "successful construction call"):
            self.diagnose()

    def test_allowlist_excludes_private_strings_and_query_outcomes(self):
        report = self.diagnose()
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertFalse(report["query_outcomes_used"])
        self.assertTrue(report["no_new_model_calls"])
        self.assertNotIn("query", report["source"]["score_distribution"])

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            write_json(output, {"original": True})
            with self.assertRaisesRegex(ValueError, "already exists"):
                diagnosis.main(["--run", directory, "--original-run", directory, "--out", str(output)])
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"original": True})


if __name__ == "__main__":
    unittest.main()
