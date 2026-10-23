"""Local HTTP tests for live-run spending admission and provider accounting."""
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest

from agent_construction_lab.fixed_model.client import ModelCallError, ModelConfig, OpenAICompatibleClient, UsageLedger
from agent_construction_lab.fixed_model.mock_server import local_mock_model, mock_manifest
from agent_construction_lab.fixed_model.study import StudyConfig, StudyExecutionError, run_study
import tests.test_fixed_client as fixture


class LiveClientBudgetTests(unittest.TestCase):
    def setUp(self):
        self.http = fixture.FixedClientTests()
        self.http.setUp()

    def tearDown(self):
        self.http.tearDown()

    def test_call_cap_blocks_before_http_and_counts_retries(self):
        self.http.responses.extend([(503, {"error": "unavailable"}), (200, fixture.response("unused"))])
        client = self.http.client(max_model_calls=1, retries=2)
        with self.assertRaises(ModelCallError) as caught:
            self.http.complete(client)
        self.assertEqual(caught.exception.error_type, "model_call_budget_exhausted")
        self.assertEqual(len(self.http.requests), 1)
        self.assertEqual(len(caught.exception.attempt_ids), 2)
        blocked = client.ledger.snapshot()[-1]
        self.assertFalse(blocked["request_sent"])
        self.assertEqual(blocked["total_tokens"], 0)
        self.assertEqual(blocked["cost_usd"], 0)
        self.assertEqual(client.ledger.totals()["model_calls"], 1)

    def test_token_cap_reserves_upper_bound_before_each_request(self):
        self.http.responses.append((200, fixture.response("ok")))
        client = self.http.client(max_output_tokens=64, max_total_tokens=1000, request_token_reserve=1000)
        self.http.complete(client)
        with self.assertRaises(ModelCallError) as caught:
            self.http.complete(client)
        self.assertEqual(caught.exception.error_type, "token_budget_exhausted")
        self.assertEqual(len(self.http.requests), 1)
        self.assertEqual(client.ledger.totals()["total_tokens"], 16)

    def test_unknown_sent_usage_aborts_without_retry_or_imputed_zero(self):
        self.http.responses.append((503, {"error": "unavailable"}))
        client = self.http.client(retries=2, max_total_tokens=3000, request_token_reserve=2000)
        with self.assertRaises(ModelCallError) as caught:
            self.http.complete(client)
        self.assertEqual(caught.exception.error_type, "budget_usage_unknown")
        self.assertEqual(len(self.http.requests), 1)
        self.assertIsNone(client.ledger.totals()["total_tokens"])
        self.assertFalse(client.ledger.snapshot()[0]["retry_scheduled"])
        self.assertEqual(client.ledger.snapshot()[0]["underlying_error_type"], "http_503")
        self.assertEqual(client.ledger.budget_status(client.config)["conservatively_reserved_tokens"], 2000)
        with self.assertRaises(ModelCallError):
            self.http.complete(client)
        self.assertEqual(len(self.http.requests), 1)

    def test_shared_ledger_admission_is_atomic_across_clients(self):
        self.http.responses.append((200, fixture.response("ok")))
        first = self.http.client(max_model_calls=1)
        second = OpenAICompatibleClient(first.config, first.ledger)
        failures = []

        def invoke(client):
            try:
                self.http.complete(client)
            except ModelCallError as error:
                failures.append(error.error_type)

        threads = [threading.Thread(target=invoke, args=(client,)) for client in (first, second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(self.http.requests), 1)
        self.assertEqual(failures, ["model_call_budget_exhausted"])

    def test_provider_exceeding_documented_bound_stops_further_requests(self):
        response = fixture.response("ok")
        response["usage"].update(prompt_tokens=1500, completion_tokens=600, total_tokens=2100)
        self.http.responses.append((200, response))
        client = self.http.client(max_total_tokens=4000, request_token_reserve=2000)
        for _ in range(2):
            with self.assertRaises(ModelCallError):
                self.http.complete(client)
        self.assertEqual(len(self.http.requests), 1)
        self.assertEqual(client.ledger.snapshot()[0]["error_type"], "token_reservation_exceeded")
        self.assertEqual(client.ledger.totals()["total_tokens"], 2100)

    def test_reopened_ledger_does_not_reset_spending_pool(self):
        self.http.responses.append((200, fixture.response("ok")))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "usage.jsonl"
            first = self.http.client(UsageLedger(path), max_model_calls=1)
            self.http.complete(first)
            reopened = OpenAICompatibleClient(first.config, UsageLedger(path))
            with self.assertRaises(ModelCallError):
                self.http.complete(reopened)
        self.assertEqual(len(self.http.requests), 1)

    def test_large_builder_prompt_is_blocked_before_http(self):
        client = self.http.client(max_output_tokens=64, request_token_reserve=1000)
        with self.assertRaises(ModelCallError) as caught:
            client.complete([{"role": "user", "content": "x" * 2000}], role="builder")
        self.assertEqual(caught.exception.error_type, "request_token_reservation_exceeded")
        self.assertEqual(self.http.requests, [])

    def test_deepseek_top_level_cache_hit_is_not_counted_twice(self):
        response = fixture.response("ok")
        response["usage"] = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
                             "prompt_cache_hit_tokens": 80, "prompt_cache_miss_tokens": 20}
        self.http.responses.append((200, response))
        client = self.http.client(input_price_per_million=2, output_price_per_million=3,
                                  cached_input_price_per_million=0.5)
        completion = self.http.complete(client)
        self.assertEqual(completion.usage["cached_input_tokens"], 80)
        self.assertEqual(completion.usage["input_tokens"], 100)
        self.assertEqual(completion.usage["total_tokens"], 120)
        self.assertAlmostEqual(completion.usage["cost_usd"], (20*2 + 80*0.5 + 20*3) / 1_000_000)
        self.assertIsNone(completion.usage["reasoning_tokens"])

    def test_conflicting_cache_accounting_is_not_a_valid_completion(self):
        response = fixture.response("ok")
        response["usage"].update(prompt_cache_hit_tokens=9, prompt_cache_miss_tokens=1)
        self.http.responses.append((200, response))
        client = self.http.client()
        with self.assertRaises(ModelCallError) as caught:
            self.http.complete(client)
        self.assertEqual(caught.exception.error_type, "invalid_provider_usage")
        self.assertFalse(client.ledger.snapshot()[0]["usage_consistent"])
        self.assertIsNone(client.ledger.snapshot()[0]["cost_usd"])

    def test_provider_resource_interruption_is_an_infrastructure_error(self):
        response = fixture.response("")
        response["choices"][0]["finish_reason"] = "insufficient_system_resource"
        self.http.responses.append((200, response))
        with self.assertRaises(ModelCallError) as caught:
            self.http.complete(self.http.client())
        self.assertEqual(caught.exception.error_type, "provider_generation_interrupted")

    def test_invalid_budget_configuration_is_rejected(self):
        for arguments in ({"max_model_calls": 0}, {"max_model_calls": True}, {"max_total_tokens": 100},
                          {"request_token_reserve": -1}, {"request_token_reserve": 1}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                ModelConfig(model="configured-model", **arguments)


class StudyBudgetTests(unittest.TestCase):
    def test_exhausted_actor_budget_preserves_failure_without_summary(self):
        with tempfile.TemporaryDirectory() as directory, local_mock_model() as model:
            root = Path(directory) / "run"
            with self.assertRaises(StudyExecutionError):
                run_study(mock_manifest(), replace(model, max_model_calls=1), root,
                          config=StudyConfig(methods=("G",), max_modules=0, bootstrap_samples=10), mock=True)
            status = json.loads((root / "run_status.json").read_text())
            self.assertFalse(status["completed"])
            self.assertTrue(status["stopped_for_budget"])
            self.assertEqual(status["usage"]["model_calls"], 1)
            self.assertFalse((root / "summary.json").exists())
            self.assertFalse((root / "source_pairs.jsonl").exists())

    def test_failed_builder_keeps_every_attempt_owned(self):
        with tempfile.TemporaryDirectory() as directory, local_mock_model() as model:
            root = Path(directory) / "run"
            # Two source tasks, no modules, two calls each; then D is blocked.
            with self.assertRaises(StudyExecutionError):
                run_study(mock_manifest(), replace(model, max_model_calls=4), root,
                          config=StudyConfig(methods=("D",), max_modules=0, bootstrap_samples=10), mock=True)
            owners = Counter()
            for name in ("episodes.jsonl", "constructions.jsonl", "failed_operations.jsonl"):
                path = root / name
                if path.exists():
                    for line in path.read_text().splitlines():
                        owners.update(json.loads(line)["attempt_ids"])
            rows = [json.loads(line) for line in (root / "model_usage.jsonl").read_text().splitlines()]
            self.assertEqual(set(owners), {row["attempt_id"] for row in rows})
            self.assertTrue(all(count == 1 for count in owners.values()))
            self.assertFalse((root / "summary.json").exists())


if __name__ == "__main__":
    unittest.main()
