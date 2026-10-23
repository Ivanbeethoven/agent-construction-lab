"""Local HTTP integration checks; mock provider results are NOT LLM evidence."""

from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from agent_construction_lab.fixed_model.client import ModelCallError, ModelConfig, OpenAICompatibleClient, UsageLedger
from agent_construction_lab.fixed_model.executor import run_episode


def response(content=None, *, tools=None, model="frozen-test-model", usage=True):
    result = {"id": "mock-response", "model": model,
              "choices": [{"message": {"role": "assistant", "content": content, "tool_calls": tools},
                           "finish_reason": "tool_calls" if tools else "stop"}]}
    if usage:
        result["usage"] = {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16,
                           "prompt_tokens_details": {"cached_tokens": 2},
                           "completion_tokens_details": {"reasoning_tokens": 4}}
    return result


def function(name, arguments, call_id="call-1"):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


class PythonEnvironment:
    """Actually executes trusted test code; its evaluator is never an actor tool."""
    instruction = "Set answer to the integer 4, then finish."
    public_context = "Python variables persist between execute calls."

    def __init__(self):
        self.namespace = {}
        self.executions = 0
        self.evaluations = 0
        self.closed = False

    def execute(self, code):
        self.executions += 1
        output = io.StringIO()
        with redirect_stdout(output):
            exec(compile(code, "<trusted-test-benchmark>", "exec"), self.namespace)
        return output.getvalue()

    def evaluate(self):
        self.evaluations += 1
        success = self.namespace.get("answer") == 4
        return {"success": success, "score": float(success), "details": {"private_evaluator_marker": "EVALUATOR_NEVER_IN_ACTOR_CONTEXT"}}

    def close(self):
        self.closed = True


class FixedClientTests(unittest.TestCase):
    def setUp(self):
        self.responses = []
        self.requests = []
        test = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                test.requests.append({"path": self.path, "body": json.loads(body), "authorization": self.headers.get("Authorization")})
                status, payload = test.responses.pop(0) if test.responses else (500, {"error": "No scripted response"})
                encoded = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("x-request-id", "mock-http-request")
                if status == 302:
                    self.send_header("Location", f"http://127.0.0.1:{test.server.server_port}/redirect-target")
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.01), daemon=True)
        self.thread.start()
        self.credentials = patch.dict(os.environ, {"ACL_TEST_API_KEY": "unit-test-value-not-a-real-key"})
        self.credentials.start()

    def tearDown(self):
        self.credentials.stop()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def client(self, ledger=None, **options):
        fields = dict(model="frozen-test-model", model_snapshot="frozen-test-model",
                      base_url=f"http://127.0.0.1:{self.server.server_port}/v1",
                      api_key_env="ACL_TEST_API_KEY", timeout=2, retries=0,
                      input_price_per_million=1.0, output_price_per_million=2.0)
        fields.update(options)
        return OpenAICompatibleClient(ModelConfig(**fields), ledger)

    def complete(self, client, **options):
        return client.complete([{"role": "user", "content": "Return one JSON action."}],
                               phase="source", role="source", domain="test", task_id="task-1", **options)

    def test_real_http_request_identity_usage_and_seed(self):
        self.responses.append((200, response('{"action":"finish","answer":"done"}')))
        with tempfile.TemporaryDirectory() as directory:
            ledger = UsageLedger(Path(directory) / "usage.jsonl")
            client = self.client(ledger, expected_response_model="frozen-test-model")
            completion = self.complete(client, seed=37)
            self.assertEqual(completion.model, "frozen-test-model")
            self.assertEqual(self.requests[0]["path"], "/v1/chat/completions")
            self.assertEqual(self.requests[0]["body"]["model"], "frozen-test-model")
            self.assertEqual(self.requests[0]["body"]["seed"], 37)
            self.assertEqual(self.requests[0]["authorization"], "Bearer unit-test-value-not-a-real-key")
            totals = ledger.totals()
            self.assertEqual(totals["total_tokens"], 16)
            self.assertEqual(totals["reasoning_tokens"], 4)
            self.assertAlmostEqual(totals["cost_usd"], 22 / 1_000_000)
            self.assertEqual(totals["model_calls"], 1)
            stored = (Path(directory) / "usage.jsonl").read_text()
            self.assertNotIn("unit-test-value-not-a-real-key", stored)
            self.assertFalse(ledger.snapshot()[0]["seed_determinism_guaranteed"])

    def test_http_retry_preserves_failed_attempt_and_unknown_usage(self):
        self.responses.extend([(503, {"error": "temporary"}), (200, response('{"action":"finish","answer":"done"}'))])
        client = self.client(retries=1)
        completion = self.complete(client)
        self.assertEqual(len(completion.attempt_ids), 2)
        rows = client.ledger.snapshot()
        self.assertEqual([row["success"] for row in rows], [False, True])
        self.assertEqual(rows[0]["error_type"], "http_503")
        self.assertTrue(rows[0]["retry_scheduled"])
        self.assertIsNone(rows[0]["input_tokens"])
        self.assertIsNone(rows[0]["cost_usd"])
        totals = client.ledger.totals()
        self.assertEqual(totals["model_calls"], 2)
        self.assertEqual(totals["known_input_tokens"], 10)
        self.assertIsNone(totals["input_tokens"])
        self.assertIsNone(totals["cost_usd"])
        self.assertEqual(totals["failed_attempts"], 1)

    def test_response_model_check_fails_without_retry_and_keeps_usage(self):
        self.responses.append((200, response('{"action":"finish","answer":"done"}', model="different-model")))
        client = self.client(expected_response_model="frozen-test-model", retries=2)
        with self.assertRaises(ModelCallError) as caught:
            self.complete(client)
        self.assertEqual(caught.exception.error_type, "response_model_identity_mismatch")
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(client.ledger.snapshot()[0]["response_model"], "different-model")
        self.assertEqual(client.ledger.totals()["total_tokens"], 16)

    def test_alias_response_can_be_recorded_without_strict_identity_claim(self):
        self.responses.append((200, response("ok", model="provider-resolved-snapshot")))
        result = self.complete(self.client())
        self.assertEqual(result.model, "provider-resolved-snapshot")

    def test_invalid_http_json_is_an_audited_failure(self):
        self.responses.append((200, b"not json"))
        client = self.client(retries=2)
        with self.assertRaises(ModelCallError) as caught:
            self.complete(client)
        self.assertEqual(caught.exception.error_type, "invalid_response_json")
        self.assertEqual(len(self.requests), 1)
        self.assertIsNone(client.ledger.totals()["total_tokens"])

    def test_credentials_are_not_forwarded_through_http_redirects(self):
        self.responses.append((302, {"message": "redirect"}))
        client = self.client()
        with self.assertRaises(ModelCallError) as caught:
            self.complete(client)
        self.assertEqual(caught.exception.error_type, "http_302")
        self.assertEqual(len(self.requests), 1)

    def test_missing_usage_stays_unknown_and_seed_can_be_unsent(self):
        self.responses.append((200, response("ok", usage=False)))
        client = self.client(send_seed=False)
        result = self.complete(client, seed=29)
        self.assertIsNone(result.usage["input_tokens"])
        self.assertIsNone(client.ledger.totals()["cost_usd"])
        self.assertNotIn("seed", self.requests[0]["body"])
        self.assertEqual(client.ledger.snapshot()[0]["request_seed"], 29)
        self.assertFalse(client.ledger.snapshot()[0]["seed_sent"])

    def test_missing_named_credential_does_not_make_a_request(self):
        client = self.client(api_key_env="ACL_NONEXISTENT_TEST_KEY")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ModelCallError):
                self.complete(client)
        self.assertEqual(self.requests, [])
        self.assertEqual(client.ledger.totals()["model_calls"], 0)
        self.assertEqual(client.ledger.totals()["failed_attempts"], 1)

    def test_executor_tool_cycle_executes_python_and_hides_terminal_evaluator(self):
        self.responses.extend([
            (200, response(tools=[function("code_execution", {"code": "answer = 2 + 2\nprint(answer)"})])),
            (200, response(tools=[function("finish", {"answer": "Completed"}, "call-2")])),
        ])
        environment = PythonEnvironment()
        client = self.client()
        result = run_episode(environment, client, {"schema_version": 1, "system_prompt": "Complete the task.", "modules": ["verify"]},
                             task_id="task-1", domain="test", max_steps=3, seed=17)
        self.assertTrue(result["success"])
        self.assertEqual(result["steps"], 2)
        self.assertEqual(result["tool_steps"], 1)
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(environment.namespace["answer"], 4)
        self.assertEqual(environment.evaluations, 1)
        self.assertTrue(environment.closed)
        sent = json.dumps([item["body"]["messages"] for item in self.requests])
        self.assertNotIn("EVALUATOR_NEVER_IN_ACTOR_CONTEXT", sent)
        self.assertEqual(self.requests[1]["body"]["messages"][-1]["role"], "tool")
        self.assertEqual(result["trace"][-1]["event"], "terminal_evaluation")

    def test_executor_json_fallback_and_old_observation_truncation(self):
        self.responses.extend([
            (200, response(json.dumps({"action": "execute", "code": "answer = 4\nprint('x' * 10000)"}))),
            (200, response(json.dumps({"action": "finish", "answer": "done"}))),
        ])
        environment = PythonEnvironment()
        result = run_episode(environment, self.client(use_tools=False), {"system_prompt": "BASE PROMPT", "modules": []},
                             task_id="task-2", domain="test", max_steps=3, max_context_chars=2200)
        self.assertTrue(result["success"])
        self.assertNotIn("tools", self.requests[0]["body"])
        first, second = [row["body"]["messages"] for row in self.requests]
        self.assertEqual(first[:2], second[:2])
        self.assertLessEqual(len(json.dumps(second, ensure_ascii=False, separators=(",", ":"))), 2200)
        self.assertTrue(any(event["event"] == "observation_truncation" for event in result["trace"]))
        self.assertGreater(len(next(event["output"] for event in result["trace"] if event["event"] == "code_execution")), 9000)

    def test_executor_invalid_action_is_failure_and_always_closes(self):
        self.responses.append((200, response("I will do something, but this is not JSON.")))
        environment = PythonEnvironment()
        result = run_episode(environment, self.client(), {"system_prompt": "Complete task", "modules": []},
                             task_id="task-3", domain="test")
        self.assertFalse(result["success"])
        self.assertEqual(result["failure_type"], "invalid_action")
        self.assertEqual(result["failure_category"], "actor_protocol")
        self.assertTrue(result["valid_task_outcome"])
        self.assertEqual(environment.executions, 0)
        self.assertEqual(environment.evaluations, 1)
        self.assertTrue(environment.closed)

    def test_executor_does_not_hide_exhausted_http_retries(self):
        self.responses.extend([(503, {"error": "temporary"}), (503, {"error": "still unavailable"})])
        environment = PythonEnvironment()
        result = run_episode(environment, self.client(retries=1), {"system_prompt": "Complete task", "modules": []},
                             task_id="task-4", domain="test")
        self.assertFalse(result["success"])
        self.assertEqual(result["failure_type"], "model_call_error")
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(result["failed_model_attempts"], 2)
        self.assertFalse(result["valid_task_outcome"])
        self.assertEqual(result["failure_category"], "infrastructure")
        self.assertIsNone(result["usage"]["input_tokens"])
        self.assertTrue(environment.closed)

    def test_last_allowed_execution_can_succeed_without_extra_finish_turn(self):
        self.responses.append((200, response(tools=[function("code_execution", {"code": "answer = 4"})])))
        result = run_episode(PythonEnvironment(), self.client(), {"system_prompt": "Complete task", "modules": []},
                             task_id="last-turn", domain="test", max_steps=1)
        self.assertTrue(result["success"])
        self.assertEqual(result["score"], 1)
        self.assertFalse(result["finished"])
        self.assertEqual(result["failure_type"], "step_budget_exhausted")
        self.assertTrue(result["valid_task_outcome"])
        self.assertFalse(result["execution_healthy"])

    def test_missing_evaluator_outcome_is_unknown_and_not_a_zero_label(self):
        class BrokenEvaluation(PythonEnvironment):
            def evaluate(self):
                raise RuntimeError("unavailable evaluator")

        self.responses.append((200, response('{"action":"finish","answer":"done"}')))
        result = run_episode(BrokenEvaluation(), self.client(), {"system_prompt": "Complete task", "modules": []},
                             task_id="broken-evaluator", domain="test")
        self.assertIsNone(result["score"])
        self.assertIsNone(result["native_score"])
        self.assertFalse(result["valid_task_outcome"])
        self.assertEqual(result["failure_category"], "infrastructure")

    def test_fixed_reasoning_and_sampling_fields_are_sent_and_recorded(self):
        self.responses.append((200, response("ok")))
        client = self.client(temperature=None, reasoning_effort="low", top_p=0.8, max_tokens_parameter="max_completion_tokens")
        self.complete(client)
        body = self.requests[0]["body"]
        self.assertNotIn("temperature", body)
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertEqual(body["top_p"], 0.8)
        self.assertIn("max_completion_tokens", body)
        self.assertEqual(client.ledger.snapshot()[0]["reasoning_effort"], "low")

    def test_timing_survives_patched_python_clocks(self):
        self.responses.append((200, response("ok")))
        client = self.client()
        with patch("time.perf_counter", return_value=0.0), patch("time.time", return_value=0.0):
            self.complete(client)
        entry = client.ledger.snapshot()[0]
        self.assertGreater(entry["latency_seconds"], 0)
        self.assertFalse(entry["timestamp_utc"].startswith("1970"))
        self.assertEqual(entry["clock_source"], "os_clock_gettime")

    def test_actual_freezegun_keeps_real_http_and_episode_time(self):
        try:
            from freezegun import freeze_time
        except ImportError:
            self.skipTest("Optional AppWorld dependency freezegun is absent in this interpreter")
        self.responses.append((200, response('{"action":"execute","code":"answer = 4"}')))
        client = self.client(use_tools=False)
        with freeze_time("2001-01-01 00:00:00"):
            frozen_start = time.perf_counter()
            result = run_episode(PythonEnvironment(), client, {"system_prompt": "Complete task", "modules": []},
                                 task_id="frozen-clock", domain="test", max_steps=1)
            self.assertEqual(time.perf_counter(), frozen_start)
        self.assertTrue(result["success"])
        self.assertGreater(result["elapsed_seconds"], 0)
        entry = client.ledger.snapshot()[0]
        self.assertGreater(entry["latency_seconds"], 0)
        self.assertFalse(entry["timestamp_utc"].startswith("2001"))

    def test_multiple_tool_calls_cannot_bypass_action_budget(self):
        self.responses.append((200, response(tools=[function("code_execution", {"code": "answer = 4"}),
                                                    function("code_execution", {"code": "answer = 5"}, "second")])) )
        environment = PythonEnvironment()
        result = run_episode(environment, self.client(), {"system_prompt": "Complete task", "modules": []},
                             task_id="task-5", domain="test", max_steps=1)
        self.assertEqual(result["failure_type"], "invalid_action")
        self.assertEqual(environment.executions, 0)


if __name__ == "__main__":
    unittest.main()
