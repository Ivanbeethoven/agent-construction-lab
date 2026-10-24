"""Synthetic diagnosis checks: no provider calls, private data, or live evidence."""
import copy
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_construction_lab._io import digest, write_json
from agent_construction_lab.fixed_model.constructors import ConstructorConfig, ConstructorLibrary, candidate_module_sets, construct_domain, make_harness
from agent_construction_lab.fixed_model.client import ModelConfig, aggregate_usage
from agent_construction_lab.fixed_model.spec import freeze_harness
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

    def fresh_fixture(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.run = Path(temp.name).resolve() / "fresh-run"
        self.run.mkdir()
        self.config = replace(self.config, methods=("G", "F", "S", "V", "G+R", "F+R"))
        model = ModelConfig(model="fixture-provider", model_snapshot="fixture-snapshot", expected_response_model="fixture-provider")
        def usage(attempt, tokens):
            return dict(attempt_id=attempt, request_sent=True, success=True, phase="construction:S" if attempt == "selector-1" else "actor",
                        input_tokens=tokens, output_tokens=0, total_tokens=tokens, cached_input_tokens=0,
                        reasoning_tokens=0, cost_usd=0.0, latency_seconds=0.0)
        self.ledger = [usage("selector-1", 10)]
        records = copy.deepcopy(self.library.records)
        for index, (row, record) in enumerate(zip(self.episodes, records)):
            episode_id, attempt_id = f"source-{index:03d}", f"actor-{index:04d}"
            physical = usage(attempt_id, record["cost"])
            self.ledger.append(physical)
            harness = make_harness(record["modules"])
            row.update(episode_id=episode_id, task_id=record["task_id"], domain=record["domain_id"], execution_seed=record["seed"],
                construction_seed=None, condition="source_intervention" if record["modules"] else "source_baseline",
                harness=harness, harness_sha256=digest(harness), attempt_ids=[attempt_id], usage=aggregate_usage([physical]))
            record.update(episode_id=episode_id, baseline_episode_id=f"source-{index-index%11:03d}")
        self.library = ConstructorLibrary.from_source(records, self.library.config)
        for index, row in enumerate(self.episodes):
            if row["stage"] != "support":
                continue
            attempt_id = f"actor-{index:04d}"
            physical = usage(attempt_id, 20)
            self.ledger.append(physical)
            row.update(episode_id=f"support-{index:03d}", attempt_ids=[attempt_id], usage=aggregate_usage([physical]))
        for row in self.builds:
            frozen = freeze_harness(self.harnesses[row["path"]], self.run / row["path"], row["metadata"])
            row["manifest_sha256"] = frozen["manifest_sha256"]
        for name, rows in (("source_pairs.jsonl", records), ("episodes.jsonl", self.episodes),
                           ("model_usage.jsonl", self.ledger), ("constructions.jsonl", self.builds), ("support_searches.jsonl", self.searches)):
            (self.run / name).write_text("".join(json.dumps(row)+"\n" for row in rows), encoding="utf-8")
        for name, value in (("task_manifest.json", self.manifest), ("study_config.json", asdict(self.config)),
                            ("model_config.json", asdict(model)), ("constructor_library.json", self.library.to_dict()),
                            ("run_status.json", dict(completed=True, mock_model=False, run_id="fresh-fixture", usage=aggregate_usage(self.ledger))),
                            ("run_provenance.json", dict(manifest_sha256=digest(self.manifest), study_config_sha256=digest(asdict(self.config)),
                                model_config_sha256=digest(asdict(model)), code_revision="a"*40, source_sha256=diagnosis._project_hashes()))):
            write_json(self.run / name, value)
        for name in ("query_results.jsonl", "summary.json", "query_lock.json"):
            (self.run / name).write_text("FORBIDDEN QUERY CONTENT " + SECRET, encoding="utf-8")
        return self.run

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

    def test_legacy_diagnostic_serialization_is_unchanged(self):
        # Captured from the pre-fresh-run implementation for this deterministic
        # synthetic fixture, and compared byte-for-byte before pinning here.
        serialized = (json.dumps(self.diagnose(), ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
        self.assertEqual(hashlib.sha256(serialized).hexdigest(), "55aa105da52f30dbbc00e84880af1a74e1694406f38f75475d38780c66c40cef")

    def test_existing_output_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            write_json(output, {"original": True})
            with self.assertRaisesRegex(ValueError, "already exists"):
                diagnosis.main(["--run", directory, "--original-run", directory, "--out", str(output)])
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"original": True})

    def test_fresh_run_without_original_is_verified_and_never_opens_query_files(self):
        self.fresh_fixture()
        original_text, original_bytes = Path.read_text, Path.read_bytes
        forbidden = {"query_results.jsonl", "summary.json", "query_lock.json"}
        def read_text(path, *args, **kwargs):
            self.assertNotIn(path.name, forbidden)
            return original_text(path, *args, **kwargs)
        def read_bytes(path, *args, **kwargs):
            self.assertNotIn(path.name, forbidden)
            return original_bytes(path, *args, **kwargs)
        with patch.object(Path, "read_text", read_text), patch.object(Path, "read_bytes", read_bytes):
            report = diagnosis.diagnose_run(self.run)
        self.assertTrue(report["evidence"]["fresh_run"])
        self.assertTrue(report["evidence"]["source_pairs_match_actual_episodes"])
        self.assertEqual(report["source"]["episodes"], 33)
        self.assertFalse(report["query_outcomes_used"])
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertNotIn("constant zero labels", " ".join(report["limitations"]))
        output = self.run.parent / "diagnosis.json"
        self.assertEqual(diagnosis.main(["--run", str(self.run), "--out", str(output)]), 0)
        self.assertTrue(output.exists())

    def test_fresh_incomplete_gate_precedes_every_other_evidence_read(self):
        self.fresh_fixture()
        write_json(self.run / "run_status.json", dict(completed=False, mock_model=False))
        original = Path.read_text
        def guarded(path, *args, **kwargs):
            self.assertEqual(path, self.run / "run_status.json")
            return original(path, *args, **kwargs)
        with patch.object(Path, "read_text", guarded), patch.object(Path, "read_bytes", side_effect=AssertionError("No hash reads before completion")):
            with self.assertRaisesRegex(ValueError, "completed non-mock"):
                diagnosis.diagnose_run(self.run)

    def test_fresh_runtime_or_configuration_mismatch_is_rejected(self):
        self.fresh_fixture()
        path = self.run / "run_provenance.json"
        original = json.loads(path.read_text())
        for field in ("source_sha256", "study_config_sha256"):
            altered = copy.deepcopy(original)
            if field == "source_sha256":
                altered[field]["agent_construction_lab/fixed_model/constructors.py"] = "0"*64
            else:
                altered[field] = "0"*64
            write_json(path, altered)
            with self.subTest(field=field), self.assertRaises(ValueError):
                diagnosis.diagnose_run(self.run)

    def test_fresh_pairs_must_match_actual_source_episodes(self):
        self.fresh_fixture()
        pairs = copy.deepcopy(self.library.records)
        pairs[1]["score"] = 1.0
        (self.run / "source_pairs.jsonl").write_text("".join(json.dumps(row)+"\n" for row in pairs), encoding="utf-8")
        write_json(self.run / "constructor_library.json", ConstructorLibrary.from_source(pairs, self.library.config).to_dict())
        with self.assertRaisesRegex(ValueError, "Source pairs differ"):
            diagnosis.diagnose_run(self.run)

    def test_fresh_frozen_artifact_and_mid_analysis_mutation_are_rejected(self):
        self.fresh_fixture()
        artifact = self.run / "G" / "harness.json"
        value = json.loads(artifact.read_text())
        value["system_prompt"] = "changed"
        write_json(artifact, value)
        with self.assertRaisesRegex(ValueError, "frozen artifact"):
            diagnosis.diagnose_run(self.run)
        freeze_harness(self.harnesses["G"], self.run / "G", self.builds[0]["metadata"])
        original = diagnosis.diagnose_data
        def mutate(*args, **kwargs):
            report = original(*args, **kwargs)
            with (self.run / "source_pairs.jsonl").open("a", encoding="utf-8") as stream:
                stream.write("\n")
            return report
        with patch.object(diagnosis, "diagnose_data", side_effect=mutate):
            with self.assertRaisesRegex(ValueError, "changed during diagnosis"):
                diagnosis.diagnose_run(self.run)

    def _accepted_round(self, *, initial, final):
        search = self.searches[0]
        condition = search["condition"]
        support = [row for row in self.episodes if row["stage"] == "support" and row["condition"] == condition]
        for index, row in enumerate(support):
            row["score"] = [initial, 0.1, 0.8][index//3]
        validation = copy.deepcopy(support[-3:])
        for row in validation:
            row["score"] = final
        self.episodes.extend(validation)
        search["audit"].update(actual_evaluations=4, candidate_evaluation_requests=4, gepa_metric_calls=4, unused_evaluation_budget=0)
        search["selected_score"] = max(initial, final)
        search["selected_harness"] = copy.deepcopy(validation[0]["harness"] if final > initial else support[0]["harness"])
        self.harnesses[condition] = search["selected_harness"]
        next(row for row in self.builds if row["condition"] == condition)["harness_sha256"] = digest(search["selected_harness"])
        return self.diagnose()["feedback_optimization"][0]

    def test_improving_third_callback_and_winning_fourth_validation_are_distinguished(self):
        result = self._accepted_round(initial=0.2, final=0.7)
        self.assertTrue(result["proposal_accepted"])
        self.assertTrue(result["proposal_wins_full_validation"])
        self.assertEqual(result["selected_score"], 0.7)
        self.assertEqual(result["callbacks"][2]["inferred_role"], "reflected_proposal")
        self.assertEqual(result["callbacks"][3]["inferred_role"], "proposal_full_validation")

    def test_improving_proposal_does_not_imply_it_wins_full_validation(self):
        result = self._accepted_round(initial=0.6, final=0.1)
        self.assertTrue(result["proposal_accepted"])
        self.assertFalse(result["proposal_wins_full_validation"])
        self.assertEqual(result["selected_score"], 0.6)
        self.assertNotEqual(result["selected_score"], result["callbacks"][2]["objective"])


if __name__ == "__main__":
    unittest.main()
