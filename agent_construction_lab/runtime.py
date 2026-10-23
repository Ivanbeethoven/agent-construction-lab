"""Stateful finite-state simulator. Reconstructed behavior, not historical replay."""
import hashlib
import random
from .contracts import RunResult, TaskSpec, candidate_harnesses


class ToolError(RuntimeError):
    def __init__(self, kind, retryable=False):
        super().__init__(kind)
        self.kind, self.retryable = kind, retryable


class SyntheticWorld:
    instances_created = 0

    def __init__(self, spec, seed):
        type(self).instances_created += 1
        self.spec, self.seed = spec, seed
        entropy = hashlib.sha256(f"{spec.task_id}:{seed}".encode()).digest()
        rng = random.Random(int.from_bytes(entropy, "big"))
        self.records = {f"rec-{i}-{rng.randrange(10000)}": rng.randrange(1, 100)
                        for i in range(spec.features["workload_size"])}
        self.fault_active = rng.random() < 0.9
        self.ledger, self.keys, self.events = [], set(), []
        self.steps = self.cost = 0
        self.faults_seen = set()
        self.saved_cursor = 0
        self.event("world_created", seed=seed, actor="simulated")

    def event(self, event, **fields):
        self.events.append(dict(event=event, index=len(self.events), cost_units=self.cost, **fields))

    def charge(self, action, tool=True):
        if self.cost >= self.spec.budget:
            raise ToolError("budget_exhausted")
        self.cost += 1
        self.steps += int(tool)
        self.event("tool_call" if tool else "local_operation", action=action)

    def list_records(self):
        self.charge("list_records")
        return list(self.records)

    def read(self, record):
        self.charge("read_record")
        if self.spec.features["transient_faults"] == "read" and self.fault_active and "read" not in self.faults_seen:
            self.faults_seen.add("read")
            raise ToolError("transient_read", True)
        key = "value" if self.spec.features["schema_variant"] == "legacy" else "amount"
        return {key: self.records[record]}

    def commit(self, record, amount, key=None):
        self.charge("commit_record")
        if key is None or key not in self.keys:
            self.ledger.append((record, amount))
            if key is not None:
                self.keys.add(key)
            self.event("committed", record=record, amount=amount)
        else:
            self.event("idempotency_hit", record=record)
        if self.spec.features["transient_faults"] == "write_after_commit" and self.fault_active and "write" not in self.faults_seen:
            self.faults_seen.add("write")
            raise ToolError("ambiguous_commit", True)


class BaseHarness:
    def __init__(self, world):
        self.world = world

    def invoke(self, function, *args):
        return function(*args)

    def prepare(self):
        pass

    def amount(self, response):
        if "amount" not in response:
            raise ToolError("schema_mismatch")
        return response["amount"]

    def key(self, record):
        return None

    def checkpoint(self, cursor):
        pass

    def restore(self):
        return 0

    def run(self):
        self.prepare()
        records = self.world.list_records()
        cursor = 0
        while cursor < len(records):
            record = records[cursor]
            value = self.amount(self.invoke(self.world.read, record))
            key = self.key(record)
            self.invoke(self.world.commit, record, value, key)
            cursor += 1
            self.checkpoint(cursor)
            if self.world.spec.features["restart_risk"] and self.world.fault_active and "restart" not in self.world.faults_seen:
                self.world.faults_seen.add("restart")
                self.world.event("worker_restarted")
                cursor = self.restore()


class RetryMixin:
    def invoke(self, function, *args):
        try:
            return super().invoke(function, *args)
        except ToolError as error:
            if not error.retryable:
                raise
            self.world.charge("retry_decision", tool=False)
            self.world.event("retry", failure_type=error.kind)
            return super().invoke(function, *args)


class IdempotencyMixin:
    def derive_idempotency_key(self, record):
        return hashlib.sha256(f"{self.world.spec.task_id}:{record}".encode()).hexdigest()

    def key(self, record):
        self.world.charge("derive_idempotency_key", tool=False)
        return self.derive_idempotency_key(record)


class CheckpointMixin:
    def checkpoint(self, cursor):
        self.world.charge("save_checkpoint", tool=False)
        self.world.saved_cursor = cursor

    def restore(self):
        self.world.event("checkpoint_restored", cursor=self.world.saved_cursor)
        return self.world.saved_cursor


class SchemaAdapterMixin:
    def prepare(self):
        super().prepare()
        self.world.charge("discover_schema")

    def amount(self, response):
        return response.get("amount", response.get("value"))


MIXINS = {"retry": RetryMixin, "idempotency": IdempotencyMixin,
          "checkpoint": CheckpointMixin, "schema_adapter": SchemaAdapterMixin}


def execute_harness(spec, mechanisms=(), seed=0, harness_class=None):
    spec = TaskSpec.from_dict(spec) if isinstance(spec, dict) else spec
    mechanisms = tuple(mechanisms)
    if mechanisms not in candidate_harnesses(spec):
        raise ValueError("Unsupported or incompatible mechanisms")
    world = SyntheticWorld(spec, seed)
    actor = harness_class or type("Harness", tuple(MIXINS[m] for m in mechanisms) + (BaseHarness,), {})
    failure = None
    try:
        actor(world).run()
    except ToolError as error:
        failure = error.kind
    counts = {record: sum(r == record for r, _ in world.ledger) for record in world.records}
    wrong = sum(amount != world.records[record] for record, amount in world.ledger)
    duplicates = sum(max(0, count - 1) for count in counts.values())
    exact = sum(count == 1 for count in counts.values())
    success = failure is None and not wrong and exact == len(world.records)
    if not success and failure is None:
        failure = "duplicate_write" if duplicates else "incomplete_work"
    score = float(success)
    world.event("terminal_evaluation", success=success, score=score, failure_type=failure)
    return RunResult(spec.task_id, spec.family, mechanisms, seed, success, score, world.cost, world.steps, failure,
                     world.events, dict(actor="simulated_finite_state_actor", real_llm_evidence=False,
                        duplicate_writes=duplicates, wrong_writes=wrong, exactly_once_records=exact,
                        records=len(world.records), ledger_entries=len(world.ledger)))


def run_episode(spec, mechanisms=(), seed=0):
    return execute_harness(spec, mechanisms, seed)
