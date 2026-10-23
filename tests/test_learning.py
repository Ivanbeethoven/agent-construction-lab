import dataclasses
import json
import unittest

from agent_construction_lab.contracts import candidate_harnesses, public_features
from agent_construction_lab.experiments import collect_source_data
from agent_construction_lab.fixtures import get_tasks
from agent_construction_lab.learning import (
    ConditionalUpliftModel, NearestGoodEndpoint, PairedObservation,
    RidgeRegressor, ScalarFinalValueModel, TrainingDataError,
    validate_training_records,
)
from agent_construction_lab.runtime import SyntheticWorld


class LearningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = collect_source_data(execution_seeds=(101, 211))
        cls.model = ConditionalUpliftModel.fit(cls.records, construction_seed=17)

    def test_source_collection_contains_noop_positive_and_negative_interventions(self):
        self.assertTrue(any(not row.mechanisms for row in self.records))
        self.assertTrue(any(row.candidate_success and not row.base_success for row in self.records))
        self.assertTrue(any(row.base_success and not row.candidate_success for row in self.records))
        self.assertTrue(any(len(row.mechanisms) == 2 for row in self.records))
        validate_training_records(self.records)

    def test_training_rejects_query_or_test_even_with_source_spelling_elsewhere(self):
        for changed in (dataclasses.replace(self.records[0], role="query"),
                        dataclasses.replace(self.records[0], split="test"),
                        dataclasses.replace(self.records[0], role="support")):
            with self.assertRaises(TrainingDataError):
                ConditionalUpliftModel.fit([changed])

    def test_training_rejects_unpaired_seeds_and_outcome_features(self):
        bad_seed = dataclasses.replace(self.records[0], provenance={"candidate_execution_seed": -1})
        bad_features = dataclasses.replace(self.records[0], features={"query_label": 1})
        for changed in (bad_seed, bad_features):
            with self.assertRaises(TrainingDataError):
                validate_training_records([changed])

    def test_source_records_and_model_roundtrip(self):
        row = self.records[11]
        self.assertEqual(PairedObservation.from_dict(json.loads(json.dumps(row.to_dict()))), row)
        restored = ConditionalUpliftModel.from_dict(json.loads(json.dumps(self.model.to_dict(), sort_keys=True)))
        task = get_tasks("test")[2]
        for mechanisms in candidate_harnesses(task):
            original = self.model.predict(public_features(task), mechanisms)
            after = restored.predict(public_features(task), mechanisms)
            self.assertAlmostEqual(original.uplift, after.uplift, places=12)
            self.assertAlmostEqual(original.objective, after.objective, places=12)

    def test_selection_does_not_create_an_environment_and_respects_candidates(self):
        before = SyntheticWorld.instances_created
        task = get_tasks("test")[6]
        candidates = candidate_harnesses(task)
        chosen = self.model.select(public_features(task), candidates)
        self.assertIn(chosen, candidates)
        self.assertNotIn("idempotency", chosen)
        self.assertEqual(SyntheticWorld.instances_created, before)
        self.assertEqual(self.model.predict(public_features(task), ()).objective, 0.0)

    def test_failure_supervision_crossfit_and_same_data_controls(self):
        endpoint = NearestGoodEndpoint.fit(self.records)
        scalar = ScalarFinalValueModel.fit(self.records)
        digest = self.model.provenance["data_sha256"]
        self.assertEqual(endpoint.provenance["data_sha256"], digest)
        self.assertEqual(scalar.provenance["data_sha256"], digest)
        self.assertEqual(self.model.provenance["failure_crossfit"], "family_grouped")
        self.assertGreaterEqual(self.model.provenance["failure_crossfit_folds"], 2)
        self.assertTrue(self.model.failures)

    def test_ridge_fits_a_known_small_linear_problem(self):
        model = RidgeRegressor.fit([[1.0, x] for x in (-2, -1, 0, 1, 2)],
                                   [1 + 2 * x for x in (-2, -1, 0, 1, 2)], alpha=0.00001)
        self.assertAlmostEqual(model.predict([1.0, 3.0]), 7.0, places=4)


if __name__ == "__main__":
    unittest.main()
