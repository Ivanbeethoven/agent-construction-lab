import json
from pathlib import Path
import tempfile
import unittest

from agent_construction_lab.contracts import RunResult, candidate_harnesses, public_features
from agent_construction_lab.experiments import (
    ExecutionLedger, PilotConfig, clustered_paired_bootstrap, collect_source_data,
    partition_target_roles, positive_repair_control, public_contract_rule, run_pilot,
)
from agent_construction_lab.fixtures import get_tasks
from agent_construction_lab.learning import TrainingDataError
from agent_construction_lab.runtime import run_episode
from agent_construction_lab.search import SupportCase, optimize_support


class ProtocolTests(unittest.TestCase):
    def test_role_partition_is_disjoint_and_seed_reproducible(self):
        tasks = get_tasks("test")
        support, query = partition_target_roles(tasks)
        repeat_support, repeat_query = partition_target_roles(tasks)
        support_ids = {task.task_id for values in support.values() for task in values}
        query_ids = {task.task_id for task in query}
        self.assertFalse(support_ids & query_ids)
        self.assertEqual(support_ids | query_ids, {task.task_id for task in tasks})
        self.assertEqual(support, repeat_support)
        self.assertEqual(query, repeat_query)

    def test_source_injected_runner_and_physical_cost_reuse(self):
        calls = []
        task = get_tasks("source")[0]
        ledger = ExecutionLedger()

        def evaluator(spec, mechanisms, *, seed):
            calls.append((spec.task_id, tuple(mechanisms), seed))
            return run_episode(spec, mechanisms, seed=seed)

        rows = collect_source_data([task], execution_seeds=(91, 92), ledger=ledger, evaluator=evaluator)
        self.assertEqual(len(calls), 2 * len(candidate_harnesses(task)))
        self.assertEqual(ledger.totals()["executions"], len(calls))
        self.assertEqual(len(rows), len(calls))
        noop = next(row for row in rows if not row.mechanisms)
        self.assertEqual(noop.provenance["base_execution_id"], noop.provenance["candidate_execution_id"])
        before = len(calls)
        with self.assertRaises(TrainingDataError):
            collect_source_data([get_tasks("test")[0]], evaluator=evaluator)
        self.assertEqual(len(calls), before)

    def test_search_uses_support_only_and_enforces_real_rollout_budget(self):
        tasks = [get_tasks("test")[0], get_tasks("test")[1]]
        calls = []

        def evaluator(spec, mechanisms, *, seed):
            calls.append((spec.task_id, tuple(mechanisms), seed))
            success = "retry" in mechanisms
            return RunResult(spec.task_id, spec.family, tuple(mechanisms), seed,
                             success, float(success), 3, 2, None if success else "missing_retry")

        for optimizer in ("greedy", "beam"):
            calls.clear()
            result = optimize_support((), [SupportCase(task) for task in tasks],
                candidate_harnesses(tasks[0]), optimizer=optimizer, candidate_budget=3,
                execution_seeds=(701, 702), evaluator=evaluator)
            self.assertLessEqual(result.candidates_evaluated, 3)
            self.assertEqual(result.support_executions, len(calls))
            self.assertEqual(len(calls), result.candidates_evaluated * len(tasks) * 2)
            self.assertEqual({task_id for task_id, _, _ in calls}, {task.task_id for task in tasks})
            self.assertEqual(result.support_tool_steps, 2 * len(calls))
        calls.clear()
        with self.assertRaises(ValueError):
            optimize_support((), [SupportCase(tasks[0], role="query")], [()], evaluator=evaluator)
        self.assertEqual(calls, [])

    def test_family_bootstrap_pairs_all_seed_axes_and_rejects_missing_rows(self):
        rows = []
        for family in ("a", "b", "c", "d"):
            for seed in (51, 52, 53):
                for method in ("left", "right"):
                    rows.append({"method": method, "task_id": family + "0", "family": family,
                                 "construction_seed": 17, "execution_seed": seed,
                                 "success": method == "left"})
        result = clustered_paired_bootstrap(rows, "left", "right", samples=40)
        self.assertEqual(result["family_clusters"], 4)
        self.assertEqual(result["paired_observations"], 12)
        self.assertEqual((result["ci_low"], result["ci_high"]), (1.0, 1.0))
        with self.assertRaises(ValueError):
            clustered_paired_bootstrap(rows[:-1], "left", "right")

    def test_handwritten_rule_uses_only_declared_faults_and_preconditions(self):
        tasks = get_tasks("test")
        ambiguous = tasks[2]
        self.assertEqual(public_contract_rule(public_features(ambiguous), candidate_harnesses(ambiguous)),
                         ("retry", "idempotency"))
        self.assertEqual(public_contract_rule(public_features(tasks[11]), candidate_harnesses(tasks[11])), ())
        self.assertEqual(public_contract_rule({"schema_variant": "legacy", "budget": 1}, [(), ("schema_adapter",)]), ())

    def test_config_prevents_seed_channel_overlap(self):
        with self.assertRaises(ValueError):
            PilotConfig.from_value({"construction_seeds": [1009]})
        with self.assertRaises(ValueError):
            PilotConfig.from_value({"support_execution_seeds": [1009]})

    def test_archive_can_find_synergistic_pair_on_matching_profile_control(self):
        ledger = ExecutionLedger()
        control = positive_repair_control(get_tasks("dev"), ledger)
        beam = next(row for row in control["rows"] if row["optimizer"] == "beam")
        self.assertEqual(beam["selected_mechanisms"], ["retry", "idempotency"])
        self.assertEqual(beam["candidates_evaluated"], 11)
        self.assertGreater(beam["repaired_query_success_rate"], beam["baseline_query_success_rate"])
        self.assertNotEqual(beam["support_task_id"], beam["query_task_id"])
        self.assertFalse(set(control["support_execution_seeds"]) & set(control["query_execution_seeds"]))
        phases = [entry["phase"] for entry in ledger.entries]
        self.assertLess(max(index for index, phase in enumerate(phases) if phase == "sanity_support"),
                        min(index for index, phase in enumerate(phases) if phase == "sanity_query"))


class PilotIntegrationTests(unittest.TestCase):
    def test_pilot_reports_actual_costs_frozen_artifacts_and_complete_four_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            config = {"source_execution_seeds": [101, 211], "support_execution_seeds": [503],
                      "query_execution_seeds": [1009], "construction_seeds": [17],
                      "support_per_family": 1, "candidate_budget": 2,
                      "bootstrap_samples": 30, "include_ablations": False}
            summary = run_pilot(directory, config)
            output = Path(directory)
            for name in ("summary.json", "splits.json", "source_pairs.jsonl", "execution_ledger.jsonl",
                         "query_results.csv", "method_summary.csv", "four_cells.csv", "paired_comparisons.csv"):
                self.assertTrue((output / name).is_file(), name)
            self.assertEqual(summary["accounting"]["construction_environment_executions"], 0)
            self.assertFalse(summary["real_llm_evidence"])
            self.assertEqual(len(summary["four_cells"]), 8)
            methods = {row["method"] for row in summary["methods"]}
            self.assertIn("public_contract_rule", methods)
            self.assertIn("conditional_uplift+beam_rsi", methods)
            ledger = [json.loads(line) for line in (output / "execution_ledger.jsonl").read_text().splitlines()]
            self.assertEqual(summary["accounting"]["all_physical_executions"]["executions"], len(ledger))
            self.assertEqual(summary["accounting"]["all_physical_executions"]["tool_steps"], sum(row["tool_steps"] for row in ledger))
            self.assertEqual(summary["accounting"]["all_physical_executions"]["cost_units"], sum(row["cost_units"] for row in ledger))
            query_ids = {row["task_id"] for row in ledger if row["role"] == "query"}
            support_ids = {row["task_id"] for row in ledger if row["role"] == "support"}
            self.assertFalse(query_ids & support_ids)
            for task_id in query_ids:
                relevant = [row for row in ledger if row["task_id"] == task_id or row["target_task_id"] == task_id]
                phases = [row["phase"] for row in relevant]
                if "support" in phases:
                    self.assertLess(max(index for index, phase in enumerate(phases) if phase == "support"),
                                    min(index for index, phase in enumerate(phases) if phase == "query"))
            constructions = [json.loads(line) for line in (output / "construction_log.jsonl").read_text().splitlines()]
            self.assertEqual(len(constructions), summary["accounting"]["artifacts_built"])
            self.assertTrue((output / constructions[0]["artifact_path"] / "agent.py").is_file())


if __name__ == "__main__":
    unittest.main()
