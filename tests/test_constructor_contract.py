"""Builder output-contract regressions; no external model calls are made."""
import json
import unittest

from agent_construction_lab.fixed_model.constructors import (
    ConstructorConfig, ConstructorLibrary, ConstructorResponseError,
    MODULES, construct_domain, make_harness,
)
from tests.test_fixed_constructors import source_records


class ConstructorContractTests(unittest.TestCase):
    def library(self, maximum=2):
        return ConstructorLibrary.from_source([row for row in source_records() if len(row["modules"]) <= maximum],
                                               ConstructorConfig(max_modules=maximum))

    def build(self, response, *, maximum=2, condition="D", seen=None):
        def model_call(prompt, role):
            if seen is not None:
                seen.append((prompt, role))
            return response
        return construct_domain(condition, domain_id="heldout-domain", public_profile={"description": "Public API contract"},
                                features={"risk": 0.0, "complexity": 0.0}, library=self.library(maximum), model_call=model_call)

    def test_direct_prompt_states_the_entire_bounded_validator_contract(self):
        seen = []
        result = self.build(json.dumps(make_harness(["verify"])), maximum=1, seen=seen)
        self.assertEqual(len(seen), 1)
        text, role = seen[0]
        payload = json.loads(text.split("\n", 1)[1])
        schema = payload["response_schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), {"schema_version", "system_prompt", "modules"})
        modules = schema["properties"]["modules"]
        self.assertEqual(modules["maxItems"], 1)
        self.assertTrue(modules["uniqueItems"])
        self.assertEqual(modules["items"], {"type": "string", "enum": list(MODULES)})
        self.assertEqual(set(payload["allowed_modules"]), set(MODULES))
        self.assertEqual(payload["example_shape"]["modules"], [])
        self.assertEqual(role, "builder")
        self.assertEqual(result["metadata"]["constructor_contract_version"], 2)

    def test_bad_module_shapes_are_rejected_without_retry_coercion_or_truncation(self):
        cases = [([{"name": "plan"}], "nonstring_module_count", 1),
                 (["invented_PRIVATE_MODULE"], "unknown_module_count", 1),
                 (["verify", "verify"], "duplicate_module_count", 1),
                 (["plan", "verify", "tool_docs"], "module_count", 3)]
        for modules, field, expected in cases:
            seen = []
            text = json.dumps({"schema_version": 1, "system_prompt": "PRIVATE_PROMPT_MUST_NOT_BE_LOGGED", "modules": modules})
            with self.subTest(modules=modules), self.assertRaises(ConstructorResponseError) as caught:
                self.build(text, seen=seen)
            self.assertEqual(len(seen), 1)
            self.assertEqual(caught.exception.diagnostics[field], expected)
            self.assertNotIn("PRIVATE_PROMPT", str(caught.exception))
            self.assertNotIn("invented_PRIVATE_MODULE", str(caught.exception))
            self.assertEqual(len(caught.exception.diagnostics["response_sha256"]), 64)

    def test_zero_module_budget_is_explicit_in_schema(self):
        seen = []
        self.build(json.dumps(make_harness()), maximum=0, seen=seen)
        payload = json.loads(seen[0][0].split("\n", 1)[1])
        self.assertEqual(payload["response_schema"]["properties"]["modules"]["maxItems"], 0)

    def test_selector_requires_one_valid_candidate_id_field(self):
        seen = []
        self.build('{"candidate_id":"c00"}', condition="S", seen=seen)
        payload = json.loads(seen[0][0].split("\n", 1)[1])
        schema = payload["response_schema"]
        self.assertEqual(schema["required"], ["candidate_id"])
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["candidate_id"]["enum"], [row["candidate_id"] for row in payload["candidate_library"]])

    def test_invalid_json_diagnostics_contain_no_response_excerpt(self):
        with self.assertRaises(ConstructorResponseError) as caught:
            self.build("PRIVATE_INVALID_RESPONSE_TEXT")
        self.assertEqual(caught.exception.diagnostics["parsed_type"], "NoneType")
        self.assertNotIn("PRIVATE_INVALID_RESPONSE_TEXT", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
