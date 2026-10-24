"""Local HTTP checks for explicit finish, bounded repair, and physical budgets.

Scripted HTTP responses are interface evidence only; no provider is contacted.
"""
import json
import unittest

from agent_construction_lab.fixed_model.client import ModelConfig
from agent_construction_lab.fixed_model.executor import run_episode, TOOLS
from tests import test_fixed_client as fixtures
from tests.test_fixed_client import PythonEnvironment, function, response


class FinishProtocolTests(unittest.TestCase):
    setUp = fixtures.FixedClientTests.setUp
    tearDown = fixtures.FixedClientTests.tearDown
    client = fixtures.FixedClientTests.client

    def run_actor(self, *, environment=None, max_steps=5, **options):
        environment = environment or PythonEnvironment()
        client = self.client(actor_protocol="v2_required_tools", **options)
        result = run_episode(environment, client, {"system_prompt": "Complete the task.", "modules": []},
                             task_id="task-1", domain="test", max_steps=max_steps)
        self.assertEqual(environment.evaluations, 1)
        self.assertTrue(environment.closed)
        self.assertNotIn("EVALUATOR_NEVER_IN_ACTOR_CONTEXT", json.dumps(self.requests))
        self.assertEqual(result["attempt_ids"], [row["attempt_id"] for row in client.ledger.snapshot()])
        return result, environment, client

    def test_required_tools_are_actor_only_and_repair_is_an_audited_request(self):
        self.responses.extend([
            (200, response(tools=[function("code_execution", {"code": "answer = 4"})])),
            (200, response("All requested changes are complete.")),
            (200, response(tools=[function("finish", {"answer": "Done"}, "finish-3")])),
        ])
        result, environment, client = self.run_actor(max_steps=3, reasoning_effort="none")
        self.assertTrue(result["success"])
        self.assertTrue(result["finished"])
        self.assertTrue(result["execution_healthy"])
        self.assertEqual(result["finish_mode"], "explicit_tool")
        self.assertEqual(result["explicit_finish_count"], 1)
        self.assertEqual(result["auto_final_count"], 0)
        self.assertEqual(result["protocol_violation_count"], 1)
        self.assertEqual(result["protocol_repairs_used"], 1)
        self.assertEqual(result["protocol_repair_requests"], 1)
        self.assertEqual(result["protocol_repair_attempt_ids"], ["attempt-00000003"])
        self.assertEqual(result["model_calls"], 3)
        self.assertEqual(result["usage"]["total_tokens"], 48)
        self.assertEqual(environment.executions, 1)
        for request in self.requests:
            body = request["body"]
            self.assertEqual(body["tool_choice"], "required")
            self.assertEqual(body["reasoning_effort"], "none")
            self.assertEqual(body["tools"], TOOLS)
            self.assertFalse(body["parallel_tool_calls"])
        correction = self.requests[2]["body"]["messages"][-1]
        self.assertEqual(correction["role"], "system")
        self.assertIn("Protocol correction", correction["content"])
        self.assertEqual([row["max_attempts"] for row in client.ledger.snapshot()], [3, 2, 1])
        self.responses.append((200, response("builder content")))
        client.complete([{"role": "user", "content": "Build a harness."}], phase="construction:D", role="builder", tools=TOOLS)
        self.assertEqual(self.requests[-1]["body"]["tool_choice"], "auto")

    def test_explicit_claimed_success_never_overrides_the_environment_score(self):
        self.responses.extend([
            (200, response("I succeeded at everything.")),
            (200, response(tools=[function("finish", {"answer": "Everything succeeded."})])),
        ])
        result, environment, _ = self.run_actor(max_steps=2)
        self.assertFalse(result["success"])
        self.assertEqual(result["score"], 0.0)
        self.assertTrue(result["finished"])
        self.assertEqual(result["failure_type"], "task_not_solved")
        self.assertEqual(environment.executions, 0)

    def test_plain_text_is_not_an_implicit_finish_and_only_one_repair_is_allowed(self):
        self.responses.extend([(200, response("Done.")), (200, response("Definitely done.")),
                               (200, response(tools=[function("finish", {"answer": "unused"})]))])
        result, _, _ = self.run_actor(max_steps=5)
        self.assertFalse(result["finished"])
        self.assertIsNone(result["finish_mode"])
        self.assertEqual(result["failure_type"], "invalid_action")
        self.assertEqual(result["protocol_violation_count"], 2)
        self.assertEqual(result["protocol_repairs_used"], 1)
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(len(self.responses), 1)

    def test_v2_does_not_accept_json_content_in_place_of_the_required_finish_tool(self):
        self.responses.extend([(200, response('{"action":"finish","answer":"done"}')),
                               (200, response(tools=[function("finish", {"answer": "explicit"})]))])
        result, _, _ = self.run_actor(max_steps=2)
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(result["finish_mode"], "explicit_tool")
        self.assertEqual(result["protocol_repairs_used"], 1)

    def test_malformed_or_multiple_tool_calls_are_never_repaired_or_executed(self):
        cases = [
            [function("code_execution", {"code": "answer = 4"}), function("finish", {"answer": "done"}, "second")],
            [function("unregistered", {"answer": "done"})],
            [{"id": "bad", "type": "function", "function": {"name": "finish", "arguments": "{"}}],
        ]
        for calls in cases:
            with self.subTest(calls=calls):
                self.responses[:] = [(200, response("Done.", tools=calls))]
                before = len(self.requests)
                result, environment, _ = self.run_actor(max_steps=3)
                self.assertEqual(len(self.requests) - before, 1)
                self.assertEqual(environment.executions, 0)
                self.assertFalse(result["finished"])
                self.assertEqual(result["protocol_repairs_used"], 0)
                self.assertEqual(result["failure_type"], "invalid_action")

    def test_repair_is_not_given_an_extra_turn_after_the_episode_budget(self):
        self.responses.extend([(200, response("Done.")), (200, response(tools=[function("finish", {"answer": "unused"})]))])
        result, _, _ = self.run_actor(max_steps=1)
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(result["protocol_repairs_used"], 0)
        self.assertFalse(result["finished"])
        self.assertEqual(len(self.responses), 1)

    def test_transport_retries_use_the_same_physical_budget_as_actions(self):
        self.responses.extend([(503, {"error": "temporary"}),
            (200, response(tools=[function("code_execution", {"code": "answer = 4"})])),
            (200, response(tools=[function("finish", {"answer": "unused"})]))])
        result, environment, client = self.run_actor(max_steps=2, retries=3)
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(result["model_calls"], 2)
        self.assertEqual(result["steps"], 1)
        self.assertEqual(environment.executions, 1)
        self.assertTrue(result["success"])
        self.assertFalse(result["finished"])
        self.assertEqual(result["failure_type"], "step_budget_exhausted")
        self.assertTrue(client.ledger.snapshot()[0]["retry_scheduled"])
        self.assertEqual(result["budget"]["step_unit"], "physical_model_requests")

    def test_retry_limit_stops_before_a_request_beyond_the_episode_budget(self):
        self.responses.extend([(503, {"error": "temporary"}), (200, response("unused"))])
        result, _, client = self.run_actor(max_steps=1, retries=3)
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(result["failure_type"], "model_call_error")
        self.assertFalse(result["valid_task_outcome"])
        self.assertFalse(client.ledger.snapshot()[0]["retry_scheduled"])
        self.assertEqual(len(self.responses), 1)

    def test_repair_transport_retries_are_all_accounted_and_still_capped(self):
        self.responses.extend([(200, response("Done.")), (503, {"error": "temporary"}),
                               (200, response(tools=[function("finish", {"answer": "done"})]))])
        result, _, _ = self.run_actor(max_steps=3, retries=3)
        self.assertEqual(result["model_calls"], 3)
        self.assertEqual(result["protocol_repairs_used"], 1)
        self.assertEqual(result["protocol_repair_requests"], 2)
        self.assertEqual(result["protocol_repair_attempt_ids"], ["attempt-00000002", "attempt-00000003"])
        self.assertTrue(result["finished"])

    def test_legacy_default_keeps_auto_tools_and_does_not_repair_prose(self):
        self.responses.extend([(200, response("Done.")), (200, response("unused"))])
        environment = PythonEnvironment()
        result = run_episode(environment, self.client(), {"system_prompt": "Complete task", "modules": []},
                             task_id="task-1", domain="test", max_steps=3)
        self.assertEqual(result["actor_protocol"], "v1")
        self.assertEqual(self.requests[0]["body"]["tool_choice"], "auto")
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(result["protocol_repairs_used"], 0)
        self.assertEqual(result["failure_type"], "invalid_action")

    def test_v2_configuration_requires_tools_and_deepseek_non_thinking(self):
        for options in ({"use_tools": False}, {"actor_protocol_max_repairs": 2}, {"actor_protocol_max_repairs": True},
                        {"base_url": "https://api.deepseek.com/v1"},
                        {"base_url": "https://api.deepseek.com/v1", "reasoning_effort": "high"}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                ModelConfig(model="fixture", actor_protocol="v2_required_tools", **options)
        model = ModelConfig(model="deepseek-flash", base_url="https://api.deepseek.com/v1",
                            reasoning_effort="none", actor_protocol="v2_required_tools")
        self.assertEqual(model.actor_protocol, "v2_required_tools")
        self.assertEqual(len(self.requests), 0)

    def test_request_attempt_limit_is_validated_before_dispatch(self):
        client = self.client()
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                client.complete([{"role": "user", "content": "test"}], max_attempts=value)
        self.assertEqual(len(self.requests), 0)
        self.assertEqual(client.ledger.snapshot(), [])

    def test_zero_repair_limit_and_truncated_content_are_not_silently_repaired(self):
        truncated = response("I may be finished")
        truncated["choices"][0]["finish_reason"] = "length"
        for payload, options in ((response("Done"), {"actor_protocol_max_repairs": 0}), (truncated, {})):
            with self.subTest(options=options):
                self.responses[:] = [(200, payload)]
                result, _, _ = self.run_actor(max_steps=3, **options)
                self.assertEqual(result["model_calls"], 1)
                self.assertEqual(result["protocol_repairs_used"], 0)
                self.assertEqual(result["failure_type"], "invalid_action")
                self.assertFalse(result["finished"])


if __name__ == "__main__":
    unittest.main()
