"""Synthetic evidence-shaped files test export integrity/privacy, not performance.

No real model, AppWorld task or in-progress study is accessed by this suite.
"""
import copy
import csv
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_construction_lab._io import append_json, digest, write_json
from agent_construction_lab.fixed_model.client import ModelConfig, aggregate_usage
from agent_construction_lab.fixed_model.constructors import make_harness
from agent_construction_lab.fixed_model.spec import freeze_harness
from agent_construction_lab.fixed_model.study import StudyConfig, summarize
from scripts.export_appworld_results import REPOSITORY, export_results, _harness_equivalence_groups
from scripts.resume_appworld_construction import _source_generation_hash, SNAPSHOT_FILES

PRIVATE = "PRIVATE_MARKER_MUST_NEVER_BE_PUBLISHED"


class AppWorldExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        # Windows CI may return an 8.3 alias for the temp directory. Compare
        # canonical paths in the guarded-read assertion, as the exporter does.
        self.root = Path(self.temp.name).resolve()
        self.run, self.out = self.root / "local-run", self.root / "public-out"
        self.run.mkdir()
        self.public_path = self.root / "public-manifest.json"
        self.config = StudyConfig(methods=("G", "F"), max_modules=1, bootstrap_samples=10)
        self.model = ModelConfig(model="fixture-provider-alias", model_snapshot="fixture-version",
                                 expected_response_model="fixture-provider-alias", api_key_env=PRIVATE,
                                 input_price_per_million=1, output_price_per_million=2, cached_input_price_per_million=0.5)
        self.public = {"schema_version": 1, "benchmark": "appworld", "official_protocol": False,
            "group_disjoint_support_query": True, "name": PRIVATE,
            "domains": {name: {"profile": {"description": PRIVATE, "untrusted_extra": PRIVATE},
                              "features": {"workflow": 1.0}} for name in ("source.apps", "target.apps")},
            "tasks": [
                {"task_id": "source_1", "generator_id": "source", "domain_id": "source.apps", "split": "source", "benchmark_split": "train"},
                {"task_id": "support_1", "generator_id": "support", "domain_id": "target.apps", "split": "support", "benchmark_split": "dev"},
                *[{"task_id": f"query_{i}", "generator_id": "query", "domain_id": "target.apps", "split": "query", "benchmark_split": "dev"}
                  for i in range(1, 4)]], "selection": {"version": "fixture-v1", "seed": 123, "private_extra": PRIVATE},
            "untrusted_extra": PRIVATE}
        self.local = copy.deepcopy(self.public)
        self.local["public_manifest_sha256"] = digest(self.public)
        self.local["domains"]["target.apps"]["profile"]["public_examples"] = [{"instruction": PRIVATE}]
        self.episodes, self.queries, self.usage, self.builds = [], [], [], []

        def add_episode(task_id, domain, stage, condition, harness, success):
            attempt_id = f"attempt-{len(self.usage)+1:08d}"
            self.usage.append({"attempt_id": attempt_id, "request_sent": True, "success": True,
                "request_model": self.model.model, "expected_response_model": self.model.expected_response_model,
                "response_model": self.model.expected_response_model, "input_tokens": 10, "output_tokens": 6,
                "total_tokens": 16, "cached_input_tokens": 2, "reasoning_tokens": 0, "cost_usd": 0.000021,
                "latency_seconds": 0.1, "untrusted_extra": PRIVATE})
            row = {"episode_id": f"episode-{len(self.episodes)+1}", "task_id": task_id, "domain": domain,
                "stage": stage, "condition": condition, "construction_seed": 17 if stage == "query" else None,
                "execution_seed": 101, "success": success, "score": float(success), "tool_steps": 1,
                "failure_type": None if success else "task_not_solved", "valid_task_outcome": True,
                "execution_healthy": True, "finished": True,
                "infrastructure_error": False, "harness": harness, "harness_sha256": digest(harness),
                "attempt_ids": [attempt_id], "trace": [{"instruction": PRIVATE, "password": PRIVATE}],
                "details": {"private_evaluator": PRIVATE}, "answer": PRIVATE}
            self.episodes.append(row)
            if stage == "query": self.queries.append(copy.deepcopy(row))

        add_episode("source_1", "source.apps", "source", "source_baseline", make_harness(), False)
        for condition in ("G", "F"):
            harness = make_harness([] if condition == "G" else ["verify"], system_prompt=PRIVATE)
            relative = f"artifacts/{condition}"
            frozen = freeze_harness(harness, self.run / relative, {"private": PRIVATE})
            self.builds.append({"path": relative, "manifest_sha256": frozen["manifest_sha256"],
                "harness_sha256": frozen["harness_sha256"], "domain": "target.apps", "condition": condition,
                "construction_seed": 17, "attempt_ids": [], "metadata": {"private": PRIVATE}})
            for i in range(1, 4):
                add_episode(f"query_{i}", "target.apps", "query", condition, harness, condition == "F")
        self._write_records()
        write_json(self.public_path, self.public)
        write_json(self.run / "task_manifest.json", self.local)
        write_json(self.run / "model_config.json", asdict(self.model))
        write_json(self.run / "study_config.json", asdict(self.config))
        write_json(self.run / "query_lock.json", {"all_constructions_frozen_before_query": True, "plans": self.builds})
        self._provenance()

    def tearDown(self):
        self.temp.cleanup()

    def _provenance(self):
        write_json(self.run / "run_provenance.json", {
            "manifest_sha256": digest(self.local), "model_config_sha256": digest(asdict(self.model)),
            "study_config_sha256": digest(asdict(self.config)), "code_revision": "a" * 40,
            "source_sha256": {path.relative_to(REPOSITORY).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in sorted((REPOSITORY / "agent_construction_lab").rglob("*.py"))},
            "private_extra": PRIVATE})

    def _write_records(self):
        for name, rows in (("episodes.jsonl", self.episodes), ("query_results.jsonl", self.queries),
                           ("model_usage.jsonl", self.usage), ("constructions.jsonl", self.builds)):
            (self.run / name).write_text("".join(json.dumps(row)+"\n" for row in rows), encoding="utf-8")
        summary = summarize(self.queries, self.episodes, self.builds, self.usage, self.config, mock=False)
        summary["untrusted_extra"] = PRIVATE
        write_json(self.run / "summary.json", summary)
        write_json(self.run / "run_status.json", {"completed": True, "mock_model": False, "usage": aggregate_usage(self.usage)})

    def export(self, **kwargs):
        return export_results(self.run, self.public_path, self.out, **kwargs)

    def _resume_fixture(self):
        """Minimal evidence-shaped recovery, without model or environment calls."""
        self.model = replace(self.model, max_model_calls=995, max_total_tokens=11990474,
                             request_token_reserve=1048576, max_output_tokens=2048)
        write_json(self.run / "model_config.json", asdict(self.model))
        self._provenance()
        self.original = self.root / "original-private-run"
        self.original.mkdir()
        old_model = replace(self.model, max_total_tokens=4990474)
        old_episode = copy.deepcopy(self.episodes[0])
        failure_attempt = copy.deepcopy(self.usage[0])
        failure_attempt.update(attempt_id="attempt-00000002", phase="construction:D", role="builder")
        old_usage = [copy.deepcopy(self.usage[0]), failure_attempt]
        for row in self.usage[1:]:
            row["attempt_id"] = f"attempt-{int(row['attempt_id'].split('-')[-1])+1:08d}"
        for row in self.episodes[1:] + self.queries:
            row["attempt_ids"] = [f"attempt-{int(value.split('-')[-1])+1:08d}" for value in row["attempt_ids"]]
        self.usage.insert(1, failure_attempt)
        failures = [{"operation": "interrupted_construction_or_reflection", "attempt_ids": [failure_attempt["attempt_id"]],
                     "valid_task_outcome": False, "error": PRIVATE}]
        pairs = [{"episode_id": old_episode["episode_id"], "private": PRIVATE}]
        library = {"source_data_sha256": digest(pairs), "private": PRIVATE}
        for directory in (self.run, self.original):
            for name, rows in (("source_pairs.jsonl", pairs), ("failed_operations.jsonl", failures)):
                (directory / name).write_text("".join(json.dumps(row)+"\n" for row in rows), encoding="utf-8")
            write_json(directory / "constructor_library.json", library)
        for name, rows in (("episodes.jsonl", [old_episode]), ("model_usage.jsonl", old_usage)):
            (self.original / name).write_text("".join(json.dumps(row)+"\n" for row in rows), encoding="utf-8")
        old_provenance = json.loads((self.run / "run_provenance.json").read_text())
        old_provenance["model_config_sha256"] = digest(asdict(old_model))
        for name, value in (("task_manifest.json", self.local), ("model_config.json", asdict(old_model)),
                            ("study_config.json", asdict(self.config)), ("run_provenance.json", old_provenance),
                            ("run_status.json", {"completed": False, "mock_model": False, "stage": "failed",
                                "stop_reason": "ConstructorResponseError", "stopped_for_budget": False,
                                "run_id": "source-run-001", "usage": aggregate_usage(old_usage)})):
            write_json(self.original / name, value)
        snapshot = {name: hashlib.sha256((self.original / name).read_bytes()).hexdigest() for name in SNAPSHOT_FILES}
        self.amendment = {"schema_version": 1, "kind": "source_only_construction_recovery",
            "original_run": str(self.original), "original_run_id": "source-run-001", "source_snapshot_sha256": snapshot,
            "source_anchor": digest(snapshot), "source_data_sha256": digest(pairs),
            "imported_counts": {"episodes": 1, "source_pairs": 1, "model_attempts": 2, "model_calls": 2, "failed_operations": 1},
            "allowed_runtime_changes": {}, "make_harness_source_sha256": _source_generation_hash(old_provenance, old_provenance["source_sha256"]),
            "original_usage": aggregate_usage(old_usage), "historical_failed_usage": aggregate_usage([failure_attempt]),
            "budget_amendment": {"global_max_model_calls": 1000, "global_max_total_tokens": 12000000,
                "external_model_calls": 5, "external_total_tokens": 9526, "old_model_max_total_tokens": 4990474,
                "new_model_max_total_tokens": 11990474, "model_max_calls": 995, "max_output_tokens": 2048,
                "peak_cost_upper_bound_usd": 5.4432, "private": PRIVATE}, "private": PRIVATE}
        self._write_records()
        self._write_amendment()
        self.probe, self.calibration = self.root / "resume-probe.json", self.root / "resume-calibration.json"
        write_json(self.probe, {"requested_model": self.model.model, "response_model": self.model.expected_response_model,
            "usage": {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10, "prompt_cache_hit_tokens": 0}})
        write_json(self.calibration, {"valid_task_outcome": True, "success": False, "score": 0.0, "tool_steps": 4,
            "failure_type": "step_budget_exhausted", "usage": {"input_tokens": 9000, "output_tokens": 516,
            "total_tokens": 9516, "cached_input_tokens": 8000, "model_calls": 4}})

    def _write_amendment(self):
        write_json(self.run / "resume_amendment.json", self.amendment)
        summary = json.loads((self.run / "summary.json").read_text())
        summary.update(resume_amendment=self.amendment, historical_failed_usage=self.amendment["historical_failed_usage"])
        write_json(self.run / "summary.json", summary)

    def _resume_export(self):
        return self.export(preflight_report=self.probe, calibration_summary=self.calibration)

    def _enable_finish_audit(self):
        self.model = replace(self.model, actor_protocol="v2_required_tools", actor_protocol_max_repairs=1)
        for row in self.episodes:
            row.update(actor_protocol="v2_required_tools", finish_mode="explicit_tool" if row["finished"] else None,
                explicit_finish_count=int(row["finished"]), auto_final_count=0, protocol_violation_count=0,
                protocol_repairs_used=0, protocol_repair_requests=0, protocol_repair_attempt_ids=[])
        self.queries = copy.deepcopy([row for row in self.episodes if row["stage"] == "query"])
        self._write_records()
        write_json(self.run / "model_config.json", asdict(self.model))
        self._provenance()

    def test_public_export_strips_all_private_fields_and_preserves_metrics(self):
        result = self.export()
        self.assertEqual(set(path.name for path in self.out.iterdir()), {"report.json", "query_metrics.csv", "README.md"})
        published = "\n".join(path.read_text(encoding="utf-8") for path in self.out.iterdir())
        self.assertNotIn(PRIVATE, published)
        for field in ("api_key_env", "private_evaluator", "instruction", '"trace"', '"system_prompt"'):
            self.assertNotIn(field, published)
        self.assertEqual(result["methods"]["F"]["domain_equal_success"], 1)
        self.assertEqual(result["methods"]["G"]["domain_equal_success"], 0)
        self.assertTrue(result["methods"]["F"]["insufficient_domains_for_strong_inference"])
        self.assertEqual(result["total_actual_usage"]["model_calls"], 7)
        self.assertEqual(result["execution_health"]["overall"]["clean_finishes"], 6)
        self.assertEqual(result["execution_health"]["overall"]["success_with_execution_issue"], 0)

    def test_incomplete_or_mock_status_is_rejected_before_reading_any_other_file(self):
        original = Path.read_text
        for status in ({"completed": False, "mock_model": False}, {"completed": True, "mock_model": True}):
            write_json(self.run / "run_status.json", status)

            def guarded(path, *args, **kwargs):
                if path != self.run / "run_status.json":
                    raise AssertionError("In-progress evidence was opened")
                return original(path, *args, **kwargs)

            with self.subTest(status=status), patch.object(Path, "read_text", guarded):
                with self.assertRaises(ValueError): self.export()
        self.assertFalse(self.out.exists())

    def test_source_hash_change_is_rejected(self):
        path = self.run / "run_provenance.json"
        value = json.loads(path.read_text())
        value["source_sha256"][next(iter(value["source_sha256"]))] = "0" * 64
        write_json(path, value)
        with self.assertRaisesRegex(ValueError, "source hashes"): self.export()

    def test_public_manifest_binding_and_roles_are_verified(self):
        altered = copy.deepcopy(self.public)
        altered["tasks"][0]["task_id"] = "source_2"
        write_json(self.public_path, altered)
        with self.assertRaisesRegex(ValueError, "manifest hash or task-role"): self.export()

    def test_task_role_mismatch_is_rejected_even_with_a_rebound_public_hash(self):
        altered = copy.deepcopy(self.public)
        altered["tasks"][-1]["task_id"] = "query_4"
        self.local["public_manifest_sha256"] = digest(altered)
        write_json(self.public_path, altered)
        write_json(self.run / "task_manifest.json", self.local)
        self._provenance()
        with self.assertRaisesRegex(ValueError, "task-role"): self.export()

    def test_duplicate_or_unowned_attempts_are_rejected(self):
        self.episodes[-1]["attempt_ids"].append(self.usage[0]["attempt_id"])
        (self.run / "episodes.jsonl").write_text("".join(json.dumps(row)+"\n" for row in self.episodes))
        with self.assertRaisesRegex(ValueError, "ownership"): self.export()

    def test_missing_query_task_fails_pairing_even_if_recorded_summary_is_recomputed(self):
        removed = self.queries.pop()
        self.episodes = [row for row in self.episodes if row["episode_id"] != removed["episode_id"]]
        self.usage = [row for row in self.usage if row["attempt_id"] not in removed["attempt_ids"]]
        for name, rows in (("episodes.jsonl", self.episodes), ("query_results.jsonl", self.queries), ("model_usage.jsonl", self.usage)):
            (self.run / name).write_text("".join(json.dumps(row)+"\n" for row in rows))
        with self.assertRaisesRegex(ValueError, "missing paired"): self.export()

    def test_changed_harness_is_rejected_before_query_metrics(self):
        path = self.run / "artifacts/F/harness.json"
        value = json.loads(path.read_text())
        value["system_prompt"] += " changed"
        write_json(path, value)
        with self.assertRaisesRegex(ValueError, "Frozen harness changed"): self.export()

    def test_summary_cannot_claim_a_score_not_supported_by_queries(self):
        path = self.run / "summary.json"
        value = json.loads(path.read_text())
        value["methods"]["G"]["domain_equal_success"] = 1
        write_json(path, value)
        with self.assertRaisesRegex(ValueError, "Recorded aggregate"): self.export()

    def test_extra_reports_are_whitelisted_and_separately_accounted(self):
        environment, probe, calibration = (self.root / name for name in ("env.json", "probe.json", "cal.json"))
        write_json(environment, {"completed": True, "model_calls": 0, "public_state_changed": True, "private": PRIVATE})
        write_json(probe, {"requested_model": self.model.model, "response_model": self.model.expected_response_model,
            "usage": {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10, "prompt_cache_hit_tokens": 0},
            "raw_response": PRIVATE})
        write_json(calibration, {"valid_task_outcome": True, "success": False, "score": 0.0, "tool_steps": 4,
            "failure_type": "step_budget_exhausted", "usage": {"input_tokens": 100, "output_tokens": 20,
            "total_tokens": 120, "cached_input_tokens": 80, "model_calls": 4}, "details": PRIVATE})
        result = self.export(environment_report=environment, preflight_report=probe, calibration_summary=calibration)
        self.assertEqual(result["extra_validation"]["additional_spend"]["model_calls"], 5)
        self.assertEqual(result["extra_validation"]["additional_spend"]["total_tokens"], 130)
        self.assertNotIn(PRIVATE, (self.out / "report.json").read_text(encoding="utf-8"))

    def test_existing_output_is_never_overwritten(self):
        self.out.mkdir()
        (self.out / "retain.txt").write_text("keep")
        with self.assertRaises(ValueError): self.export()
        self.assertEqual((self.out / "retain.txt").read_text(), "keep")

    def test_unregistered_episode_stage_is_not_published_as_an_aggregate_key(self):
        self.episodes[0]["stage"] = PRIVATE
        (self.run / "episodes.jsonl").write_text("".join(json.dumps(row)+"\n" for row in self.episodes))
        with self.assertRaisesRegex(ValueError, "task/domain/stage"): self.export()
        self.assertFalse(self.out.exists())

    def test_resumed_run_accounts_imported_failed_call_exactly_once_and_redacts_paths(self):
        self._resume_fixture()
        report = self._resume_export()
        self.assertEqual(report["total_actual_usage"]["model_calls"], 8)
        self.assertEqual(report["imported_original_usage"]["model_calls"], 2)
        self.assertEqual(report["new_run_usage"]["model_calls"], 6)
        self.assertEqual(report["historical_failed_usage"]["model_calls"], 1)
        self.assertEqual(report["historical_failed_usage"]["failed_attempts"], 0)  # HTTP succeeded; JSON contract failed.
        self.assertEqual(report["source_usage"]["model_calls"], 1)
        self.assertEqual(report["extra_validation"]["additional_spend"]["total_tokens"], 9526)
        published = "\n".join(path.read_text(encoding="utf-8") for path in self.out.iterdir())
        self.assertNotIn(PRIVATE, published)
        self.assertNotIn("original-private-run", published)
        self.assertNotIn('"original_run"', published)

    def test_resume_origin_tampering_breaks_anchored_snapshot(self):
        self._resume_fixture()
        with (self.original / "model_usage.jsonl").open("a") as stream:
            stream.write("\n")
        with self.assertRaisesRegex(ValueError, "snapshot hash"):
            self._resume_export()

    def test_resume_import_prefix_cannot_be_rewritten_or_historical_call_dropped(self):
        self._resume_fixture()
        self.usage.pop(1)
        self._write_records()
        self._write_amendment()
        with self.assertRaisesRegex(ValueError, "attempt prefix"):
            self._resume_export()

    def test_resume_failure_owner_cannot_be_omitted_or_duplicated(self):
        self._resume_fixture()
        (self.run / "failed_operations.jsonl").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "historical failure owners"):
            self._resume_export()

    def test_resume_historical_attempt_cannot_also_belong_to_new_construction(self):
        self._resume_fixture()
        self.builds[0]["attempt_ids"] = ["attempt-00000002"]
        self._write_records()
        self._write_amendment()
        write_json(self.run / "query_lock.json", {"all_constructions_frozen_before_query": True, "plans": self.builds})
        with self.assertRaisesRegex(ValueError, "ownership"):
            self._resume_export()

    def test_resume_budget_or_summary_cannot_drop_historical_cost(self):
        self._resume_fixture()
        self.amendment["historical_failed_usage"] = aggregate_usage([])
        self._write_amendment()
        with self.assertRaisesRegex(ValueError, "historical failure usage"):
            self._resume_export()

    def test_resume_requires_the_external_validation_costs_in_the_declared_budget(self):
        self._resume_fixture()
        with self.assertRaisesRegex(ValueError, "Extra validation reports"):
            self.export()

    def test_resume_rejects_unapproved_model_configuration_change(self):
        self._resume_fixture()
        self.model = replace(self.model, temperature=0.5)
        write_json(self.run / "model_config.json", asdict(self.model))
        self._provenance()
        with self.assertRaisesRegex(ValueError, "other than the token limit"):
            self._resume_export()

    def test_resume_rejects_an_amendment_without_an_anchored_original(self):
        self._resume_fixture()
        self.amendment["source_anchor"] = "0" * 64
        self._write_amendment()
        with self.assertRaisesRegex(ValueError, "source anchor"):
            self._resume_export()

    def test_resume_rejects_runtime_changes_outside_the_two_allowed_files(self):
        self._resume_fixture()
        path = self.original / "run_provenance.json"
        original_provenance = json.loads(path.read_text())
        changed = "agent_construction_lab/fixed_model/executor.py"
        original_provenance["source_sha256"][changed] = "0" * 64
        write_json(path, original_provenance)
        self.amendment["source_snapshot_sha256"][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.amendment["source_anchor"] = digest(self.amendment["source_snapshot_sha256"])
        self.amendment["allowed_runtime_changes"] = {changed: {"before": "0" * 64,
            "after": hashlib.sha256((REPOSITORY / changed).read_bytes()).hexdigest()}}
        self._write_amendment()
        with self.assertRaisesRegex(ValueError, "runtime changes"):
            self._resume_export()

    def test_resume_rejects_a_budget_amendment_omitting_extra_spend(self):
        self._resume_fixture()
        self.amendment["budget_amendment"]["global_max_total_tokens"] = 11990474
        self._write_amendment()
        with self.assertRaisesRegex(ValueError, "budget amendment"):
            self._resume_export()

    def test_terminal_success_is_reported_separately_from_unhealthy_unfinished_execution(self):
        for index, row in enumerate(self.queries):
            row.update(execution_healthy=False, finished=False,
                       failure_type="step_budget_exhausted" if index == 0 else "invalid_action")
        self.episodes = [self.episodes[0]] + copy.deepcopy(self.queries)
        self._write_records()
        result = self.export()
        overall = result["execution_health"]["overall"]
        self.assertEqual(overall["query_episodes"], 6)
        self.assertEqual(overall["valid_task_outcome"], {"true": 6, "false": 0})
        self.assertEqual(overall["execution_healthy"], {"true": 0, "false": 6})
        self.assertEqual(overall["finished"], {"true": 0, "false": 6})
        self.assertEqual(overall["failure_types"], {"invalid_action": 5, "step_budget_exhausted": 1})
        self.assertEqual(overall["task_successes"], 3)
        self.assertEqual(overall["success_with_execution_issue"], 3)
        self.assertEqual(result["execution_health"]["by_condition"]["G"]["success_with_execution_issue"], 0)
        self.assertEqual(result["execution_health"]["by_condition"]["F"]["success_with_execution_issue"], 3)
        self.assertEqual(result["methods"]["F"]["domain_equal_success"], 1)
        readme = (self.out / "README.md").read_text(encoding="utf-8")
        self.assertIn("官方终态评分", readme)
        self.assertIn("成功不代表健康完成", readme)
        self.assertIn("终态成功 3 次，其中 3 次", readme)
        csv_header = (self.out / "query_metrics.csv").read_text(encoding="utf-8").splitlines()[0]
        for field in ("valid_task_outcome", "execution_healthy", "finished"):
            self.assertIn(field, csv_header)

    def test_identical_harness_conditions_are_grouped_despite_different_scores(self):
        harness = copy.deepcopy(self.queries[0]["harness"])
        frozen = freeze_harness(harness, self.run / self.builds[1]["path"], {"private": PRIVATE})
        self.builds[1].update(manifest_sha256=frozen["manifest_sha256"], harness_sha256=frozen["harness_sha256"])
        for row in self.queries:
            row.update(harness=copy.deepcopy(harness), harness_sha256=digest(harness))
        self.episodes = [self.episodes[0]] + copy.deepcopy(self.queries)
        self._write_records()
        write_json(self.run / "query_lock.json", {"all_constructions_frozen_before_query": True, "plans": self.builds})
        result = self.export()
        groups = result["harness_equivalence_groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["conditions"], ["F", "G"])
        self.assertEqual(groups[0]["query_episodes"], 6)
        self.assertTrue(groups[0]["shared_across_conditions"])
        self.assertEqual(result["methods"]["G"]["domain_equal_success"], 0)
        self.assertEqual(result["methods"]["F"]["domain_equal_success"], 1)
        self.assertIn("其执行成绩差异不能归因为方法增益", (self.out / "README.md").read_text(encoding="utf-8"))

    def test_equivalence_groups_do_not_merge_different_domains_or_construction_seeds(self):
        row = self.queries[0]
        rows = [dict(row, domain=domain, construction_seed=seed) for domain, seed in
                (("target.apps", 17), ("other.apps", 17), ("target.apps", 18))]
        self.assertEqual(len(_harness_equivalence_groups(rows)), 3)

    def test_missing_execution_health_is_rejected_instead_of_inferred_from_task_success(self):
        del self.queries[0]["execution_healthy"]
        self.episodes = [self.episodes[0]] + copy.deepcopy(self.queries)
        self._write_records()
        with self.assertRaisesRegex(ValueError, "health flags"):
            self.export()

    def test_legacy_finish_audit_remains_unknown_instead_of_becoming_zero(self):
        result = self.export()
        audit = result["execution_health"]["overall"]["finish_audit"]
        self.assertEqual(audit["unknown_legacy_episodes"], 6)
        self.assertIsNone(audit["explicit_finish_count"])
        self.assertIsNone(audit["protocol_repairs_used"])
        self.assertEqual(result["model_config"]["actor_protocol"], "v1")
        self.assertEqual(result["stage_execution_health"]["source"]["episodes"], 1)
        self.assertEqual(result["stage_execution_health"]["support"]["episodes"], 0)
        with (self.out / "query_metrics.csv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["finish_audit_available"], "False")
        self.assertEqual(rows[0]["explicit_finish_count"], "")

    def test_v2_stage_health_and_finish_counts_are_recomputed_from_owned_episodes(self):
        self._enable_finish_audit()
        support = copy.deepcopy(self.episodes[0])
        support.update(episode_id="support-episode", task_id="support_1", domain="target.apps", stage="support", condition="F",
                       execution_healthy=False, finished=False, finish_mode=None, explicit_finish_count=0,
                       failure_type="invalid_action", protocol_violation_count=2, protocol_repairs_used=1, protocol_repair_requests=1)
        attempts = []
        for index in range(2):
            attempt = copy.deepcopy(self.usage[0])
            attempt["attempt_id"] = f"attempt-{len(self.usage)+1:08d}"
            self.usage.append(attempt)
            attempts.append(attempt["attempt_id"])
        support.update(attempt_ids=attempts, protocol_repair_attempt_ids=[attempts[-1]])
        self.episodes.append(support)
        self._write_records()
        result = self.export()
        self.assertEqual(result["model_config"]["actor_protocol"], "v2_required_tools")
        self.assertEqual(result["model_config"]["actor_protocol_max_repairs"], 1)
        stages = result["stage_execution_health"]
        self.assertEqual(stages["source"]["finish_audit"]["explicit_finish_count"], 1)
        self.assertEqual(stages["query"]["finish_audit"]["explicit_finish_count"], 6)
        self.assertEqual(stages["support"]["episodes"], 1)
        self.assertEqual(stages["support"]["execution_healthy"], {"true": 0, "false": 1})
        self.assertEqual(stages["support"]["finish_audit"]["protocol_violation_count"], 2)
        self.assertEqual(stages["support"]["finish_audit"]["protocol_repair_requests"], 1)
        self.assertEqual(stages["support"]["finish_audit"]["finish_modes"], {"unfinished": 1})
        with (self.out / "query_metrics.csv").open(encoding="utf-8", newline="") as stream:
            row = next(csv.DictReader(stream))
        self.assertEqual(row["finish_mode"], "explicit_tool")
        self.assertEqual(row["explicit_finish_count"], "1")
        self.assertEqual(row["protocol_repair_requests"], "0")
        self.assertNotIn(PRIVATE, (self.out / "report.json").read_text(encoding="utf-8"))

    def test_v2_finish_audit_is_required_for_every_episode(self):
        self.model = replace(self.model, actor_protocol="v2_required_tools")
        write_json(self.run / "model_config.json", asdict(self.model))
        self._provenance()
        with self.assertRaisesRegex(ValueError, "Incomplete finish protocol"):
            self.export()

    def test_finish_modes_and_counter_types_cannot_publish_untrusted_values(self):
        self._enable_finish_audit()
        for field, value in (("finish_mode", PRIVATE), ("protocol_violation_count", True), ("explicit_finish_count", -1)):
            with self.subTest(field=field):
                before = self.episodes[0][field]
                self.episodes[0][field] = value
                self._write_records()
                with self.assertRaises(ValueError):
                    self.export()
                self.episodes[0][field] = before
        self.assertFalse(self.out.exists())

    def test_claimed_protocol_repair_count_must_match_the_physical_ledger(self):
        self._enable_finish_audit()
        self.episodes[0].update(protocol_violation_count=1, protocol_repairs_used=1, protocol_repair_requests=1,
                                protocol_repair_attempt_ids=[])
        self._write_records()
        with self.assertRaisesRegex(ValueError, "differs from physical requests"):
            self.export()

    def test_protocol_repair_cannot_claim_another_episodes_call(self):
        self._enable_finish_audit()
        self.episodes[0].update(protocol_violation_count=1, protocol_repairs_used=1, protocol_repair_requests=1,
                                protocol_repair_attempt_ids=self.episodes[1]["attempt_ids"])
        self._write_records()
        with self.assertRaisesRegex(ValueError, "belong uniquely"):
            self.export()

    def test_protocol_outcome_counts_cannot_exceed_owned_physical_requests(self):
        self._enable_finish_audit()
        self.episodes[0]["protocol_violation_count"] = 2
        self._write_records()
        with self.assertRaisesRegex(ValueError, "outcomes exceed"):
            self.export()

    def test_calibration_publishes_only_safe_v2_finish_and_health_fields(self):
        self._enable_finish_audit()
        calibration = self.root / "v2-calibration.json"
        write_json(calibration, {"valid_task_outcome": True, "success": True, "score": 1.0, "tool_steps": 1,
            "execution_healthy": True, "finished": True, "failure_type": None,
            "actor_protocol": "v2_required_tools", "finish_mode": "explicit_tool", "explicit_finish_count": 1,
            "auto_final_count": 0, "protocol_violation_count": 1, "protocol_repairs_used": 1, "protocol_repair_requests": 1,
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cached_input_tokens": 80, "model_calls": 2},
            "instruction": PRIVATE, "trace": PRIVATE, "answer": PRIVATE, "protocol_repair_attempt_ids": [PRIVATE]})
        result = self.export(calibration_summary=calibration)
        safe = result["extra_validation"]["calibration"]
        self.assertTrue(safe["finish_audit_available"])
        self.assertEqual(safe["protocol_repair_requests"], 1)
        self.assertEqual(safe["actor_protocol"], "v2_required_tools")
        self.assertEqual(safe["finish_mode"], "explicit_tool")
        self.assertTrue(safe["execution_healthy"])
        self.assertTrue(safe["finished"])
        self.assertNotIn("protocol_repair_attempt_ids", safe)
        self.assertNotIn(PRIVATE, (self.out / "report.json").read_text(encoding="utf-8"))

    def test_manifest_limitations_count_two_source_generators_without_claiming_feature_diversity(self):
        task = {"task_id": "secondsource_1", "generator_id": "secondsource", "domain_id": "source.apps",
                "split": "source", "benchmark_split": "train"}
        self.public["tasks"].append(task)
        self.local["tasks"].append(copy.deepcopy(task))
        self.local["public_manifest_sha256"] = digest(self.public)
        episode = copy.deepcopy(self.episodes[0])
        attempt = copy.deepcopy(self.usage[0])
        attempt["attempt_id"] = f"attempt-{len(self.usage)+1:08d}"
        episode.update(episode_id="second-source", task_id="secondsource_1", attempt_ids=[attempt["attempt_id"]])
        self.usage.append(attempt)
        self.episodes.append(episode)
        self._write_records()
        write_json(self.public_path, self.public)
        write_json(self.run / "task_manifest.json", self.local)
        self._provenance()
        result = self.export()
        design = result["manifest_design"]
        self.assertEqual(design["source"], {"tasks": 2, "generators": 2, "domains": 1, "public_feature_vectors": 1})
        self.assertTrue(design["constant_public_features"])
        self.assertEqual(design["query"]["tasks"], 3)
        text = "\n".join(result["limitations"])
        self.assertIn("2 个 generator", text)
        self.assertNotIn("一个源 generator", text)
        self.assertIn("同一公开特征向量", text)
        self.assertIn("不能证明 F 的条件学习或迁移收益", text)
        self.assertIn("2 个 generator", (self.out / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
