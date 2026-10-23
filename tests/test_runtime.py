import unittest

from agent_construction_lab.contracts import TaskSpec, candidate_harnesses, public_features
from agent_construction_lab.fixtures import get_tasks, load_fixture_splits
from agent_construction_lab.runtime import run_episode


class RuntimeTests(unittest.TestCase):
    def task(self, index):
        return get_tasks("source")[index]

    def test_actual_retry_recovers_read_and_records_events(self):
        task = self.task(1)
        self.assertFalse(run_episode(task, (), 5).success)
        improved = run_episode(task, ("retry",), 5)
        self.assertTrue(improved.success)
        self.assertTrue(any(e["event"] == "retry" for e in improved.events))

    def test_retry_write_interaction_requires_idempotency(self):
        task = self.task(2)
        retry = run_episode(task, ("retry",), 3)
        both = run_episode(task, ("retry", "idempotency"), 3)
        self.assertFalse(retry.success)
        self.assertGreater(retry.metrics["duplicate_writes"], 0)
        self.assertTrue(both.success)
        self.assertEqual(both.metrics["duplicate_writes"], 0)

    def test_checkpoint_restores_real_cursor(self):
        task = self.task(3)
        self.assertFalse(run_episode(task, (), 11).success)
        result = run_episode(task, ("checkpoint",), 11)
        self.assertTrue(result.success)
        self.assertTrue(any(e["event"] == "checkpoint_restored" for e in result.events))

    def test_schema_and_unnecessary_overhead(self):
        self.assertFalse(run_episode(self.task(4), (), 4).success)
        self.assertTrue(run_episode(self.task(4), ("schema_adapter",), 4).success)
        self.assertTrue(run_episode(self.task(5), (), 4).success)
        over = run_episode(self.task(5), ("schema_adapter",), 4)
        self.assertFalse(over.success)
        self.assertEqual(over.failure_type, "budget_exhausted")

    def test_interface_gates_and_reset(self):
        task = self.task(6)
        self.assertFalse(any("idempotency" in x for x in candidate_harnesses(task)))
        with self.assertRaises(ValueError):
            run_episode(task, ("idempotency",), 1)
        first = run_episode(self.task(2), ("retry", "idempotency"), 1)
        second = run_episode(self.task(2), ("retry", "idempotency"), 1)
        self.assertEqual(first.to_dict(), second.to_dict())

    def test_public_contract_and_family_split(self):
        splits = load_fixture_splits()
        groups = {key: {task.family for task in tasks} for key, tasks in splits.items()}
        self.assertFalse(groups["source"] & groups["test"])
        self.assertFalse(groups["dev"] & groups["test"])
        self.assertNotIn("seed", public_features(self.task(0)))
        self.assertEqual(TaskSpec.from_dict(self.task(0).to_dict()), self.task(0))

    def test_declared_risk_does_not_reveal_actual_fault_occurrence(self):
        task = self.task(1)
        before = public_features(task)
        observed = {run_episode(task, (), seed).success for seed in range(30)}
        self.assertEqual(observed, {False, True})
        self.assertEqual(public_features(task), before)

    def test_different_tasks_do_not_reuse_identical_hidden_records(self):
        tasks = get_tasks("source")
        a = run_episode(tasks[0], (), 7)
        b = run_episode(tasks[8], (), 7)
        records_a = {event["record"] for event in a.events if event["event"] == "committed"}
        records_b = {event["record"] for event in b.events if event["event"] == "committed"}
        self.assertNotEqual(records_a, records_b)


if __name__ == "__main__":
    unittest.main()

