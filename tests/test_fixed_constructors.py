import copy
import json
import unittest

from agent_construction_lab.fixed_model.constructors import (
    BASE_PROMPT, MODULES, ConstructorConfig, ConstructorDataError,
    ConstructorLibrary, ConstructorResponseError, candidate_module_sets,
    construct_domain, make_harness,
)


def source_records():
    rows = []
    for domain_index in range(8):
        risk = float(domain_index % 2)
        complexity = float((domain_index // 2) % 2)
        for seed in (31, 47):
            baseline = 0.45 if seed == 31 else 0.55
            for modules in candidate_module_sets():
                effect = 0.0
                if "plan" in modules:
                    effect += 0.05
                if "verify" in modules:
                    effect += 0.30 if risk else -0.30
                if "tool_docs" in modules:
                    effect += 0.10 * complexity
                if "error_repair" in modules:
                    effect -= 0.10
                if "plan" in modules and "verify" in modules:
                    effect += 0.08 * risk
                score = baseline + effect
                rows.append({"task_id": f"source-task-{domain_index}", "domain_id": f"source-domain-{domain_index}",
                    "split": "source", "seed": seed, "features": {"risk": risk, "complexity": complexity},
                    "modules": list(modules), "baseline_success": False, "success": score >= 0.9,
                    "baseline_score": baseline, "score": score, "baseline_cost": 100 + seed,
                    "cost": 100 + seed + 10 * len(modules),
                    "trace": [{"role": "tool", "content": "source-only observation"}]})
    return rows


class FixedConstructorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = source_records()
        cls.library = ConstructorLibrary.from_source(cls.records, config=ConstructorConfig(ridge_alpha=0.05))

    def construct(self, condition, **overrides):
        inputs = {"domain_id": "held-out-domain", "public_profile": {"description": "Public transaction API contract"},
                  "features": {"risk": 0.0, "complexity": 0.0}, "library": self.library}
        inputs.update(overrides)
        return construct_domain(condition, **inputs)

    def test_generic_and_candidate_vocabulary_are_bounded(self):
        result = self.construct("G")
        self.assertEqual(result["harness"], {"schema_version": 1, "system_prompt": BASE_PROMPT, "modules": []})
        self.assertEqual(len(candidate_module_sets()), 11)
        self.assertEqual(len(candidate_module_sets(1)), 5)
        self.assertEqual(make_harness(["verify", "plan"])["modules"], ["plan", "verify"])
        with self.assertRaises(ValueError):
            make_harness(["plan", "verify", "tool_docs"])
        with self.assertRaises(ValueError):
            ConstructorLibrary.from_source(self.records, config=ConstructorConfig(max_modules=3))
        self.assertFalse(result["metadata"]["numeric_predictor_is_additional_LLM"])

    def test_any_non_source_record_is_rejected(self):
        for label, role in (("query", "source"), ("test", "source"), ("source", "support")):
            records = copy.deepcopy(self.records)
            records[-1]["split"] = label
            records[-1]["role"] = role
            with self.assertRaises(ConstructorDataError):
                ConstructorLibrary.from_source(records)

    def test_paired_baseline_requires_real_noop_consistent_outcome_and_seed(self):
        variants = []
        records = copy.deepcopy(self.records)
        records[1]["baseline_score"] += 0.01
        variants.append(records)
        records = copy.deepcopy(self.records)
        records[1]["baseline_seed"] = -1
        variants.append(records)
        variants.append(self.records[1:])
        for records in variants:
            with self.assertRaises(ConstructorDataError):
                ConstructorLibrary.from_source(records)

    def test_source_features_are_domain_level_and_have_no_outcomes(self):
        records = copy.deepcopy(self.records)
        records[1]["features"]["risk"] = 100
        with self.assertRaises(ConstructorDataError):
            ConstructorLibrary.from_source(records)
        records = copy.deepcopy(self.records)
        records[0]["features"]["query_label"] = 1
        with self.assertRaises(ConstructorDataError):
            ConstructorLibrary.from_source(records)
        with self.assertRaises(ConstructorDataError):
            self.construct("F", public_profile={"query_labels": [True]})
        with self.assertRaises(ConstructorDataError):
            self.construct("F", features={"risk": 0.0})

    def test_effect_retains_harm_and_zero_and_value_uses_identical_candidates_and_class(self):
        effect = self.construct("F")
        value = self.construct("V")
        self.assertEqual(effect["metadata"]["candidate_library"], value["metadata"]["candidate_library"])
        effect_model = effect["metadata"]["numeric_learner"]["metadata"]
        value_model = value["metadata"]["numeric_learner"]["metadata"]
        self.assertEqual(effect_model["feature_names"], value_model["feature_names"])
        self.assertEqual(effect_model["training_data_sha256"], value_model["training_data_sha256"])
        self.assertGreater(effect_model["negative_score_effect_rows"], 0)
        self.assertGreater(effect_model["zero_score_effect_rows"], 0)
        harmful = next(row for row in effect["metadata"]["predictions"] if row["modules"] == ["verify"])
        self.assertLess(harmful["predicted_score_effect"], 0)
        self.assertNotIn("verify", effect["harness"]["modules"])
        noop = next(row for row in effect["metadata"]["predictions"] if row["modules"] == [])
        self.assertEqual(noop["predicted_score_effect"], 0.0)

    def test_negative_and_nonpositive_ablations_refit_without_changing_library(self):
        full = self.construct("F")
        no_negative = self.construct("F", ablation="no_negatives")
        positive_only = self.construct("F", ablation="no_nonpositive")
        for result in (no_negative, positive_only):
            self.assertEqual(full["metadata"]["candidate_library"], result["metadata"]["candidate_library"])
            details = result["metadata"]["numeric_learner"]["metadata"]
            self.assertTrue(details["refitted_after_filtering"])
            self.assertLess(details["training_rows"], len(self.records))
            self.assertEqual(details["negative_score_effect_rows"], 0)
        self.assertGreater(no_negative["metadata"]["numeric_learner"]["metadata"]["zero_score_effect_rows"], 0)
        self.assertEqual(positive_only["metadata"]["numeric_learner"]["metadata"]["zero_score_effect_rows"], 0)
        self.assertNotEqual(full["metadata"]["numeric_learner"]["score"]["coefficients"],
                            no_negative["metadata"]["numeric_learner"]["score"]["coefficients"])

    def test_condition_and_interaction_ablations_change_the_function_class(self):
        low = self.construct("F", ablation="no_conditions")
        high = self.construct("F", ablation="no_conditions", features={"risk": 1.0, "complexity": 1.0})
        self.assertEqual(low["metadata"]["predictions"], high["metadata"]["predictions"])
        full = self.construct("F")["metadata"]["numeric_learner"]["metadata"]["feature_names"]
        reduced = self.construct("F", ablation="no_interactions")["metadata"]["numeric_learner"]["metadata"]["feature_names"]
        self.assertLess(len(reduced), len(full))

    def test_direct_and_selector_share_source_evidence_and_use_one_model_call(self):
        calls = []

        def direct(prompt, role):
            calls.append((prompt, role))
            return json.dumps(make_harness(["plan"], "Use the public domain contract."))

        def selector(prompt, role):
            calls.append((prompt, role))
            return '{"candidate_id":"c01"}'

        d = self.construct("D", model_call=direct)
        s = self.construct("S", model_call=selector)
        self.assertEqual(d["metadata"]["source_evidence"], s["metadata"]["source_evidence"])
        self.assertEqual(len(calls), 2)
        self.assertEqual([role for _, role in calls], ["builder", "builder"])
        for result in (d, s):
            self.assertEqual(result["metadata"]["constructor_model_calls"], 1)
            self.assertEqual(result["metadata"]["target_environment_rollouts"], 0)
            self.assertFalse(result["metadata"]["query_outcomes_used"])
        self.assertEqual(s["harness"]["system_prompt"], BASE_PROMPT)
        self.assertIn("not a complete paper reproduction", s["metadata"]["baseline_scope"])

    def test_invalid_model_outputs_raise_without_silent_fallback(self):
        bad_direct = ["not JSON", "[]", '{"schema_version":1,"system_prompt":"ok","modules":["unknown"]}',
                      '{"schema_version":1,"system_prompt":"ok","modules":["plan","plan"]}',
                      '{"schema_version":1,"system_prompt":"ok","modules":[],"answer":"leak"}']
        for text in bad_direct:
            with self.assertRaises(ConstructorResponseError):
                self.construct("D", model_call=lambda prompt, role, response=text: response)
        for text in ('{"candidate_id":"missing"}', '{"modules":["plan"]}', '{"candidate_id":"c00","extra":true}'):
            with self.assertRaises(ConstructorResponseError):
                self.construct("S", model_call=lambda prompt, role, response=text: response)

    def test_unknown_usage_requires_zero_penalty_and_is_not_filled_with_zero(self):
        records = copy.deepcopy(self.records)
        for row in records:
            row["baseline_cost"] = None
            row["cost"] = None
        library = ConstructorLibrary.from_source(records, config=ConstructorConfig(cost_weight=0.0))
        result = self.construct("F", library=library)
        self.assertTrue(all(row["predicted_token_effect"] is None for row in result["metadata"]["predictions"]))
        self.assertEqual(result["metadata"]["numeric_learner"]["metadata"]["unknown_cost_rows"], len(records))
        with self.assertRaises(ConstructorDataError):
            ConstructorLibrary.from_source(records, config=ConstructorConfig(cost_weight=0.01))

    def test_library_roundtrip_hash_and_mutation_guard(self):
        payload = json.loads(json.dumps(self.library.to_dict()))
        restored = ConstructorLibrary.from_dict(payload)
        self.assertEqual(self.construct("F")["harness"], self.construct("F", library=restored)["harness"])
        altered = copy.deepcopy(payload)
        altered["source_data_sha256"] = "incorrect"
        with self.assertRaises(ConstructorDataError):
            ConstructorLibrary.from_dict(altered)
        restored.records[-1]["split"] = "query"
        with self.assertRaises(ConstructorDataError):
            self.construct("F", library=restored)


if __name__ == "__main__":
    unittest.main()
