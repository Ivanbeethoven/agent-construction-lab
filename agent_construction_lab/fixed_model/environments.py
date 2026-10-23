"""Stateful integration fixture and explicit official AppWorld adapter."""
from __future__ import annotations

import ast
from collections import defaultdict
from importlib.metadata import PackageNotFoundError, version
import json
from pathlib import Path
import re

from .code_policy import CodePolicyError, CodePolicyState, validated_public_code

APPWORLD_VERSION = "0.1.3.post1"


class BuiltinEnvironment:
    """Small, deterministic API fixture; never a research benchmark substitute."""
    def __init__(self, task, **kwargs):
        payload = dict(task["payload"])
        self.item_id = payload["item_id"]
        self.quantity = payload["initial_quantity"]
        self.target_quantity = payload["target_quantity"]
        if (not isinstance(self.item_id, str) or type(self.quantity) is not int or
                type(self.target_quantity) is not int or min(self.quantity, self.target_quantity) < 0):
            raise ValueError("Invalid inventory fixture payload")
        self.writes = 0
        self.completed = False
        self.closed = False
        self.instruction = "Fixture inventory task: " + json.dumps(payload, ensure_ascii=False) + "\nSet the item's quantity to target_quantity, then complete the task."
        self.public_context = (
            "Integration fixture only. Use Python statements calling these APIs with literal keyword arguments: "
            "apis.inventory.get_item(item_id: str); apis.inventory.set_quantity(item_id: str, quantity: int); "
            "apis.supervisor.complete_task(); apis.supervisor.show_active_task(); "
            "apis.api_docs.show_app_descriptions(). You may wrap API calls in print(...). "
            "Assignments, imports, loops and dynamic arguments are unavailable in this fixture."
        )

    def _validate(self, code):
        tree = ast.parse(code)
        operations = []
        for statement in tree.body:
            if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
                raise ValueError("Only explicit API-call statements are supported")
            call = statement.value
            if isinstance(call.func, ast.Name) and call.func.id == "print":
                if len(call.args) != 1 or call.keywords or not isinstance(call.args[0], ast.Call):
                    raise ValueError("print requires one public API call")
                call = call.args[0]
            func = call.func
            if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Attribute) and
                    isinstance(func.value.value, ast.Name) and func.value.value.id == "apis"):
                raise ValueError("Only public API calls are supported")
            key = (func.value.attr, func.attr)
            signatures = {("inventory", "get_item"): {"item_id"},
                          ("inventory", "set_quantity"): {"item_id", "quantity"},
                          ("supervisor", "complete_task"): set(),
                          ("supervisor", "show_active_task"): set(),
                          ("api_docs", "show_app_descriptions"): set()}
            if key not in signatures or call.args or {kw.arg for kw in call.keywords} != signatures[key] or len(call.keywords) != len(signatures[key]):
                raise ValueError("Unknown API or arguments")
            arguments = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords}
            if "item_id" in arguments and not isinstance(arguments["item_id"], str):
                raise ValueError("item_id must be a string")
            if "quantity" in arguments and (type(arguments["quantity"]) is not int or arguments["quantity"] < 0):
                raise ValueError("quantity must be a nonnegative integer")
            operations.append((key, arguments))
        if not operations:
            raise ValueError("No API statements provided")
        return operations

    def execute(self, code):
        if self.closed:
            raise RuntimeError("Environment is closed")
        try:
            operations = self._validate(code)
            output = []
            for (app, name), arguments in operations:
                if app == "inventory":
                    if arguments["item_id"] != self.item_id:
                        raise ValueError("Unknown inventory item")
                    if name == "set_quantity":
                        self.quantity = arguments["quantity"]
                        self.writes += 1
                    output.append({"item_id": self.item_id, "quantity": self.quantity})
                elif app == "supervisor":
                    if name == "complete_task":
                        self.completed = True
                    output.append({"status": "completed" if self.completed else "in_progress"})
                else:
                    output.append({"inventory": "Read and change one inventory item", "supervisor": "Mark task completion"})
            return json.dumps(output, ensure_ascii=False)
        except (SyntaxError, ValueError, TypeError) as error:
            return json.dumps({"error": str(error)}, ensure_ascii=False)

    def evaluate(self):
        if self.closed:
            raise RuntimeError("Environment is closed")
        success = self.completed and self.quantity == self.target_quantity
        return {"success": success, "score": float(success), "details": {"fixture_only": True, "writes": self.writes}}

    def close(self):
        self.closed = True


class AppWorldEnvironment:
    """Official 0.1.3.post1 runtime. Instantiate inside the dedicated worker."""
    def __init__(self, task, *, appworld_root, experiment_name, seed=101, max_steps=15, timeout_seconds=30, **kwargs):
        try:
            installed = version("appworld")
        except PackageNotFoundError as error:
            raise RuntimeError(f"Expected appworld=={APPWORLD_VERSION}; package is not installed") from error
        if installed != APPWORLD_VERSION:
            raise RuntimeError(f"Expected appworld=={APPWORLD_VERSION}, found {installed}")
        from appworld import AppWorld, update_root
        update_root(str(Path(appworld_root).resolve()))
        self.closed = False
        self.policy_state = CodePolicyState()
        self.world = AppWorld(task_id=task["task_id"], experiment_name=experiment_name,
                              random_seed=seed, max_interactions=max_steps, timeout_seconds=timeout_seconds,
                              load_ground_truth=True, ground_truth_mode="minimal",
                              raise_on_unsafe_syntax=True, null_patch_unsafe_execution=True)
        try:
            self.instruction = self.world.task.instruction
            supervisor = self.world.task.supervisor
            public_user = {key: getattr(supervisor, key) for key in ("first_name", "last_name", "email", "phone_number")}
            self.public_context = (
                "Use public apis.<app>.<method>(...) calls. Variables persist between execute calls. "
                "Discover apps with apis.api_docs.show_app_descriptions(), endpoints with "
                "apis.api_docs.show_api_descriptions(app_name=...), and schemas with "
                "apis.api_docs.show_api_doc(app_name=..., api_name=...). "
                "Print useful API responses. Use apis.supervisor.show_account_passwords() for account access. "
                "Signal completion with apis.supervisor.complete_task().\n"
                + json.dumps({"supervisor": public_user, "datetime": str(self.world.task.datetime)}, ensure_ascii=False)
            )
        except BaseException:
            self.world.close()
            self.closed = True
            raise

    def execute(self, code):
        if self.closed:
            raise RuntimeError("Environment is closed")
        try:
            next_state = validated_public_code(code, self.policy_state)
        except CodePolicyError as error:
            return "ExecutionPolicyError: " + str(error)
        output = self.world.execute(code)
        # Keep the conservative old state after a runtime error: assignments
        # later in the block may not have executed. Reassignment remains legal.
        if "Traceback" not in output and "Execution failed" not in output:
            self.policy_state = next_state
        return output

    def evaluate(self):
        if self.closed:
            raise RuntimeError("Environment is closed")
        tracker = self.world.evaluate()
        success = tracker.success
        if type(success) is not bool:
            raise TypeError("Official evaluator returned an invalid success value")
        return {"success": success, "score": float(success),
                "details": {"official_terminal_evaluator": True, "metric": "task_goal_completion",
                            "appworld_version": APPWORLD_VERSION}}

    def close(self):
        if not self.closed:
            self.closed = True
            self.world.close()


def create_environment(benchmark, task, **kwargs):
    if benchmark == "builtin":
        return BuiltinEnvironment(task, **kwargs)
    if benchmark == "appworld":
        from .appworld_process import AppWorldProcessEnvironment
        return AppWorldProcessEnvironment(task, **kwargs)
    raise ValueError(f"Unknown benchmark: {benchmark}")


def validate_appworld_membership(manifest, appworld_root):
    from appworld import load_task_ids, update_root
    update_root(str(Path(appworld_root).resolve()))
    official = {name: set(load_task_ids(name)) for name in ("train", "dev", "test_normal", "test_challenge")}
    for task in manifest["tasks"]:
        if task.get("task_id") not in official.get(task.get("benchmark_split"), set()):
            raise ValueError("Manifest task does not belong to its declared official AppWorld split")
    return True


def prepare_appworld_manifest(appworld_root):
    """Build a deterministic *custom* train/dev study, without test feedback.

    Generators, not task instances, are partitioned between source/dev/target.
    Target profiles are derived only from their support instruction. Official
    test sets are deliberately excluded; this is not the leaderboard protocol.
    """
    from appworld import load_task_ids, update_root
    from .spec import validate_manifest
    root = Path(appworld_root).resolve()
    update_root(str(root))
    grouped = {}
    for official in ("train", "dev"):
        groups = defaultdict(list)
        for task_id in sorted(load_task_ids(official)):
            groups[task_id.rsplit("_", 1)[0]].append(task_id)
        grouped[official] = groups
    train_generators = sorted(grouped["train"])
    if len(train_generators) < 2 or not grouped["dev"]:
        raise ValueError("Insufficient AppWorld generators for disjoint source/dev/target roles")
    dev_count = max(1, len(train_generators) // 5)
    source_generators = set(train_generators[:-dev_count])
    domains, tasks = {}, []
    for official, groups in grouped.items():
        for generator, ids in sorted(groups.items()):
            if official == "dev" and len(ids) < 2:
                raise ValueError("Each target generator needs support and query task instances")
            role = ("source" if generator in source_generators else "dev") if official == "train" else "support"
            domain_id = f"{role if official == 'train' else 'target'}.{generator}"
            # specs.json is the public instruction, never ground_truth metadata.
            public = json.loads((root / "data" / "tasks" / ids[0] / "specs.json").read_text(encoding="utf-8"))
            instruction = public["instruction"]
            domains[domain_id] = {"profile": {"description": instruction, "profile_source": "public source/dev/support instruction"},
                                  "features": {"instruction_words": float(len(instruction.split())),
                                               "write_operations": float(len(re.findall(r"\b(add|create|delete|remove|update|send|mark|move|set)\b", instruction.lower())))}}
            support_count = max(1, len(ids) // 2)
            for index, task_id in enumerate(ids):
                split = role if official == "train" else ("support" if index < support_count else "query")
                tasks.append({"task_id": task_id, "generator_id": generator, "domain_id": domain_id,
                              "split": split, "benchmark_split": official})
    return validate_manifest({"schema_version": 1, "benchmark": "appworld", "official_protocol": False,
                              "name": "Custom generator-disjoint AppWorld train/dev study", "domains": domains, "tasks": tasks})
