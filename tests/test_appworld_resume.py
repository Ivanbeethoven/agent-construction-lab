"""Source-only recovery boundaries, using synthetic files and fully mocked IO.

No real run, credential database, WSL process, AppWorld data or provider is read.
"""
from collections import Counter
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_construction_lab._io import append_json, canonical_json, digest, write_json
from agent_construction_lab.fixed_model.client import Completion, ModelConfig, aggregate_usage
from agent_construction_lab.fixed_model.constructors import ConstructorConfig, ConstructorLibrary, candidate_module_sets, make_harness
from agent_construction_lab.fixed_model.study import StudyConfig, StudyExecutionError, run_study
from scripts import resume_appworld_construction as recovery


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def lines(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def physical(model, *, phase, role, domain, task_id, seed):
    return dict(request_sent=True, success=True, error_type=None, usage_consistent=True,
        input_tokens=2, output_tokens=1, total_tokens=3, cached_input_tokens=0, reasoning_tokens=0,
        latency_seconds=0.01, cost_usd=0.000001, request_model=model.model,
        response_model=model.expected_response_model, expected_response_model=model.expected_response_model,
        base_url=model.base_url, phase=phase, role=role, domain=domain, task_id=task_id, request_seed=seed)


class ResumeFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="acl-source-recovery-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "original"
        self.source.mkdir()
        self.out = self.root / "continued"
        self.config = StudyConfig(bootstrap_samples=2)
        self.old_model = ModelConfig(model="test-fixed", model_snapshot="test-fixed-v1", expected_response_model="test-fixed",
            base_url="https://example.invalid/v1", api_key_env=None, max_output_tokens=2048,
            max_model_calls=995, max_total_tokens=4_990_474, request_token_reserve=1_048_576)
        self.model = replace(self.old_model, max_total_tokens=11_990_474)
        self.manifest = dict(schema_version=1, benchmark="appworld", group_disjoint_support_query=True,
            domains={name: dict(profile=dict(description="Synthetic public test domain"), features=dict(write=1))
                     for name in ("source", "target")},
            tasks=[dict(task_id=f"source_{i}", generator_id="source", domain_id="source", split="source", benchmark_split="train")
                   for i in range(1, 4)] + [
                dict(task_id="support_1", generator_id="support", domain_id="target", split="support", benchmark_split="dev"),
                dict(task_id="query_1", generator_id="query", domain_id="target", split="query", benchmark_split="dev")])
        self.usage = []
        self.episodes = []
        self.pairs = []
        for task in self.manifest["tasks"][:3]:
            baseline = None
            for modules in candidate_module_sets(2):
                condition = "source_intervention" if modules else "source_baseline"
                calls = []
                for _ in range(12):
                    call = physical(self.old_model, phase="source:" + condition, role="source",
                                    domain="source", task_id=task["task_id"], seed=101)
                    call["attempt_id"] = f"attempt-{len(self.usage)+1:08d}"
                    self.usage.append(call)
                    calls.append(call)
                index = len(self.episodes)
                harness = make_harness(modules)
                episode = dict(episode_id=f"episode-{index:07d}", stage="source", condition=condition,
                    domain="source", task_id=task["task_id"], execution_seed=101, construction_seed=None,
                    phase="source:" + condition, role="source", harness=harness, harness_sha256=digest(harness),
                    valid_task_outcome=True, execution_healthy=False, infrastructure_error=False,
                    failure_category="budget", failure_type="step_budget", success=bool(index % 2), score=float(index % 2),
                    attempt_ids=[row["attempt_id"] for row in calls], usage=aggregate_usage(calls), tool_steps=12, trace=[])
                if baseline is None:
                    baseline = episode
                self.episodes.append(episode)
                self.pairs.append(dict(task_id=task["task_id"], domain_id="source", split="source", role="source",
                    seed=101, baseline_seed=101, features={"write": 1}, modules=list(modules),
                    baseline_success=baseline["success"], success=episode["success"], baseline_score=baseline["score"], score=episode["score"],
                    baseline_cost=baseline["usage"]["total_tokens"], cost=episode["usage"]["total_tokens"],
                    baseline_episode_id=baseline["episode_id"], episode_id=episode["episode_id"]))
        last = physical(self.old_model, phase="construction:D", role="builder", domain="target", task_id=None, seed=17)
        last["attempt_id"] = "attempt-00000397"
        self.usage.append(last)
        self.failures = [dict(operation="interrupted_construction_or_reflection", attempt_ids=[last["attempt_id"]],
                             error=recovery.CONSTRUCTION_ERROR, valid_task_outcome=False)]
        self.library = ConstructorLibrary.from_source(self.pairs, ConstructorConfig(2.0, 0.0, 2))
        for name, rows in (("episodes.jsonl", self.episodes), ("source_pairs.jsonl", self.pairs),
                           ("model_usage.jsonl", self.usage), ("failed_operations.jsonl", self.failures)):
            self.write_lines(name, rows)
        for name, value in (("task_manifest.json", self.manifest), ("model_config.json", asdict(self.old_model)),
                ("study_config.json", asdict(self.config)), ("constructor_library.json", self.library.to_dict()),
                ("run_status.json", dict(completed=False, mock_model=False, stage="failed", stopped_for_budget=False,
                    stop_reason="ConstructorResponseError", error=recovery.CONSTRUCTION_ERROR,
                    run_id="synthetic-original", usage=aggregate_usage(self.usage))),
                ("run_provenance.json", dict(manifest_sha256=digest(self.manifest), model_config_sha256=digest(asdict(self.old_model)),
                    study_config_sha256=digest(asdict(self.config)), source_sha256=recovery._source_hashes(), code_revision="0" * 40))):
            write_json(self.source / name, value)
        self.anchor = recovery.snapshot_anchor(self.source)
        # Any accidental external process or network operation fails the test.
        for target in ("socket.create_connection", "subprocess.Popen"):
            guard = patch(target, side_effect=AssertionError("External execution forbidden in recovery tests"))
            guard.start()
            self.addCleanup(guard.stop)

    def write_lines(self, name, rows):
        (self.source / name).write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")

    def prepare(self, **kwargs):
        return recovery.prepare_resume(self.source, self.manifest, kwargs.pop("model", self.model),
            kwargs.pop("config", self.config), anchor=kwargs.pop("anchor", self.anchor), **kwargs)


class SourceRecoveryValidationTests(ResumeFixture):
    def test_accepts_all_source_outcomes_and_accounts_for_old_semantic_failure(self):
        before = recovery._snapshot(self.source)
        payload = self.prepare()
        data = payload.materialize(self.manifest, self.model, self.config)
        self.assertEqual(data["episodes"], self.episodes)
        self.assertEqual(data["pairs"], self.pairs)
        self.assertEqual(data["usage"], self.usage)
        self.assertEqual(data["failed_operations"], self.failures)
        self.assertEqual(data["amendment"]["historical_failed_usage"]["model_calls"], 1)
        self.assertEqual(data["amendment"]["historical_failed_usage"]["failed_attempts"], 0)
        self.assertEqual(recovery._snapshot(self.source), before)
        self.assertEqual(data["amendment"]["budget_amendment"]["model_max_calls"] + 5, 1000)
        self.assertEqual(data["amendment"]["budget_amendment"]["new_model_max_total_tokens"] + 9526, 12_000_000)

    def test_anchor_rejects_original_evidence_changes(self):
        with (self.source / "episodes.jsonl").open("a", encoding="utf-8") as stream:
            stream.write("\n")
        with self.assertRaisesRegex(ValueError, "anchor mismatch"):
            self.prepare()

    def test_payload_rechecks_evidence_and_configuration_before_execution(self):
        payload = self.prepare()
        with self.assertRaisesRegex(ValueError, "configuration changed"):
            payload.materialize(self.manifest, replace(self.model, temperature=0.5), self.config)
        with (self.source / "model_usage.jsonl").open("a", encoding="utf-8") as stream:
            stream.write("\n")
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            payload.materialize(self.manifest, self.model, self.config)

    def test_missing_pair_or_source_episode_rejected_even_with_fresh_anchor(self):
        self.write_lines("source_pairs.jsonl", self.pairs[:-1])
        with self.assertRaisesRegex(ValueError, "33 source"):
            self.prepare(anchor=recovery.snapshot_anchor(self.source))

    def test_pair_reference_seed_and_harness_mismatch_are_rejected(self):
        for field, value in (("baseline_episode_id", "episode-0000001"), ("baseline_seed", 99), ("modules", ["verify"])):
            rows = json.loads(json.dumps(self.pairs))
            rows[0][field] = value
            self.write_lines("source_pairs.jsonl", rows)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "pairs"):
                self.prepare(anchor=recovery.snapshot_anchor(self.source))

    def test_harness_change_and_invalid_outcome_are_rejected(self):
        for change in (dict(harness_sha256="0" * 64), dict(valid_task_outcome=False), dict(failure_category="infrastructure")):
            rows = json.loads(json.dumps(self.episodes))
            rows[0].update(change)
            self.write_lines("episodes.jsonl", rows)
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.prepare(anchor=recovery.snapshot_anchor(self.source))

    def test_any_query_support_or_frozen_artifact_rejects_resume(self):
        for name in ("query_results.jsonl", "query_lock.json", "constructions.jsonl", "support_searches.jsonl"):
            path = self.source / name
            path.write_text("", encoding="utf-8")
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "support/query"):
                recovery.snapshot_anchor(self.source)
            path.unlink()

    def test_wrong_stop_reason_and_failure_owner_are_rejected(self):
        status = read(self.source / "run_status.json")
        status["stop_reason"] = "token_budget_exhausted"
        write_json(self.source / "run_status.json", status)
        with self.assertRaisesRegex(ValueError, "malformed first D"):
            self.prepare(anchor=recovery.snapshot_anchor(self.source))
        status["stop_reason"] = "ConstructorResponseError"
        write_json(self.source / "run_status.json", status)
        self.write_lines("failed_operations.jsonl", [])
        with self.assertRaisesRegex(ValueError, "failure owner"):
            self.prepare(anchor=recovery.snapshot_anchor(self.source))

    def test_budget_cannot_omit_external_calls_or_change_any_model_setting(self):
        for changes in (dict(max_model_calls=1000), dict(max_total_tokens=12_000_000), dict(temperature=0.3), dict(max_output_tokens=4096)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.prepare(model=replace(self.model, **changes))
        with self.assertRaisesRegex(ValueError, "Study configuration"):
            self.prepare(config=replace(self.config, execution_seeds=(202,)))

    def test_unapproved_runtime_change_or_file_addition_is_rejected(self):
        hashes = recovery._source_hashes()
        for name in ("agent_construction_lab/fixed_model/executor.py", "agent_construction_lab/new.py"):
            changed = dict(hashes, **{name: "0" * 64})
            with patch.object(recovery, "_source_hashes", return_value=changed):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Runtime"):
                    self.prepare()

    def test_source_function_and_module_guidance_changes_are_rejected(self):
        name = "agent_construction_lab/fixed_model/constructors.py"
        original = (recovery.REPOSITORY / name).read_text(encoding="utf-8")
        variants = (original.replace('    from .spec import validate_harness', '    # changed source function\n    from .spec import validate_harness', 1),
                    original.replace('Plan the required operations', 'Different module instructions'))
        for variant in variants:
            provenance = read(self.source / "run_provenance.json")
            provenance["source_sha256"][name] = hashlib.sha256(variant.encode("utf-8")).hexdigest()
            write_json(self.source / "run_provenance.json", provenance)
            with patch.object(recovery.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, stdout=variant.encode("utf-8"))):
                with self.subTest(variant=variant[:30]), self.assertRaisesRegex(ValueError, "Source"):
                    self.prepare(anchor=recovery.snapshot_anchor(self.source))


class SourceRecoveryOrchestrationTests(ResumeFixture):
    def install_runtime(self, fail_construction=False):
        calls = []

        def complete(client, messages, **kwargs):
            row = physical(self.model, **{key: kwargs[key] for key in ("phase", "role", "domain", "task_id", "seed")})
            attempt = client.ledger.record(row)
            return Completion("{}", [], self.model.model, {}, [attempt])

        def construct(condition, **kwargs):
            calls.append(condition)
            if condition in {"D", "S"}:
                kwargs["model_call"]("Synthetic construction", "builder")
            if fail_construction:
                raise RuntimeError("Synthetic construction failure")
            return dict(harness=make_harness(), metadata={})

        def episode(environment, client, harness, **kwargs):
            self.assertFalse(kwargs["phase"].startswith("source:"), "Source must never execute again")
            row = physical(self.model, **{key: kwargs[key] for key in ("phase", "role", "domain", "task_id", "seed")})
            attempt = client.ledger.record(row)
            return dict(valid_task_outcome=True, success=False, score=0.0, tool_steps=1, trace=[],
                        failure_type="synthetic_task_failure", attempt_ids=[attempt], usage=aggregate_usage([row]))

        def optimize(harness, evaluate, reflect, **kwargs):
            evaluate(harness)
            return dict(selected_harness=harness, audit={})

        for target, replacement in (("validate_appworld_membership", lambda *args: None),
                ("create_environment", lambda *args, **kwargs: object()), ("run_episode", episode),
                ("construct_domain", construct), ("optimize_harness", optimize)):
            mocked = patch("agent_construction_lab.fixed_model.study." + target, replacement)
            mocked.start()
            self.addCleanup(mocked.stop)
        mocked = patch("agent_construction_lab.fixed_model.client.OpenAICompatibleClient.complete", complete)
        mocked.start()
        self.addCleanup(mocked.stop)
        return calls

    def test_continuation_runs_all_conditions_without_repeating_source_or_spend(self):
        payload = self.prepare()
        before = recovery._snapshot(self.source)
        calls = self.install_runtime()
        summary = run_study(self.manifest, self.model, self.out, config=self.config, source_resume=payload)
        self.assertEqual(calls, ["D", "F", "G", "S", "V"])
        self.assertEqual(set(summary["methods"]), {"G", "D", "S", "V", "F", "G+R@4", "F+R@4"})
        usage, episodes = lines(self.out / "model_usage.jsonl"), lines(self.out / "episodes.jsonl")
        self.assertEqual(usage[:397], self.usage)
        self.assertEqual(episodes[:33], self.episodes)
        self.assertEqual(usage[397]["attempt_id"], "attempt-00000398")
        self.assertEqual(episodes[33]["episode_id"], "episode-0000033")
        self.assertEqual(lines(self.out / "source_pairs.jsonl"), self.pairs)
        self.assertEqual(read(self.out / "constructor_library.json"), self.library.to_dict())
        self.assertEqual(lines(self.out / "failed_operations.jsonl"), self.failures)
        self.assertEqual(summary["total_actual_usage"], aggregate_usage(usage))
        self.assertEqual(summary["historical_failed_usage"], aggregate_usage([self.usage[-1]]))
        owners = Counter(a for row in episodes + lines(self.out / "constructions.jsonl") + self.failures for a in row["attempt_ids"])
        self.assertEqual(len(owners), len(usage))
        self.assertEqual(set(owners.values()), {1})
        self.assertEqual(recovery._snapshot(self.source), before)

    def test_new_construction_failure_keeps_old_failure_owner_once(self):
        payload = self.prepare()
        self.install_runtime(fail_construction=True)
        with self.assertRaises(StudyExecutionError):
            run_study(self.manifest, self.model, self.out, config=self.config, source_resume=payload)
        failures = lines(self.out / "failed_operations.jsonl")
        self.assertEqual(failures[0], self.failures[0])
        self.assertEqual(failures[1]["attempt_ids"], ["attempt-00000398"])
        self.assertEqual(read(self.out / "run_status.json")["usage"]["model_calls"], 398)

    def test_changed_payload_evidence_or_output_cannot_modify_original(self):
        payload = self.prepare()
        for output in (self.source, self.source / "child"):
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, "independent"):
                run_study(self.manifest, self.model, output, config=self.config, source_resume=payload)
        corrupted = replace(payload, payload_json=payload.payload_json + " ")
        # Canonical content hashes deliberately ignore irrelevant JSON whitespace;
        # modifying actual evidence must invalidate the seal.
        data = json.loads(corrupted.payload_json)
        data["episodes"][0]["score"] = 999
        corrupted = replace(payload, payload_json=canonical_json(data))
        with self.assertRaisesRegex(ValueError, "integrity"):
            run_study(self.manifest, self.model, self.out, config=self.config, source_resume=corrupted)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
