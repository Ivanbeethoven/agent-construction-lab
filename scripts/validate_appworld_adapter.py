"""No-LLM check of the real AppWorld process/execute/evaluate/reset adapter.

Only train IDs and public API observations are read. No gold solution or private
evaluation assertions are exposed or copied into the report. This is an
environment integration check, not evidence that a model solved the benchmark.
"""
from __future__ import annotations

import argparse
from collections import Counter
from importlib.metadata import version
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_construction_lab.fixed_model.client import real_monotonic, real_timestamp_utc
from agent_construction_lab.fixed_model.environments import create_environment, prepare_appworld_manifest, validate_appworld_membership


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--appworld-root", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    root = str(Path(args.appworld_root).resolve())
    os.environ.setdefault("APPWORLD_CACHE", str(Path(root) / ".cache"))
    from appworld import load_task_ids, update_root
    update_root(root)
    manifest = prepare_appworld_manifest(root)
    validate_appworld_membership(manifest, root)
    task_id = load_task_ids("train")[0]
    task = {"task_id": task_id}
    readings = []
    started = real_monotonic()
    for repeat in range(2):
        env = create_environment("appworld", task, appworld_root=root,
            experiment_name=f"acl-environment-check-{repeat}", seed=101,
            max_steps=6, timeout_seconds=30, initialization_timeout_seconds=60)
        try:
            docs = env.execute("print(apis.api_docs.show_app_descriptions())")
            before = env.execute("print(apis.supervisor.show_active_task())")
            result_before = env.evaluate()
            after = None
            if repeat == 0:
                env.execute("print(apis.supervisor.complete_task())")
                after = env.execute("print(apis.supervisor.show_active_task())")
            policy_rejection = env.execute("open('/etc/hostname').read()")
            readings.append({"docs": docs, "before": before, "after": after,
                "result": result_before, "blocked_file_read": policy_rejection.startswith("ExecutionPolicyError:"),
                "context_characters": len(env.public_context)})
        finally:
            env.close()
    results = {
        "completed": True, "timestamp_utc": real_timestamp_utc(), "model_calls": 0,
        "validation_scope": "Real AppWorld adapter only; no LLM, no task-solution claim",
        "appworld_version": version("appworld"), "gepa_version": version("gepa"),
        "source_subset": "train", "task_id": task_id, "fresh_worlds": 2,
        "official_scoring_observed": all(r["result"]["details"].get("official_terminal_evaluator") for r in readings),
        "initial_score": readings[0]["result"]["score"],
        "public_api_execution_nonempty": all(bool(r["docs"]) and "Traceback" not in r["docs"] for r in readings),
        "public_state_changed": readings[0]["before"] != readings[0]["after"],
        "reset_restored_initial_public_state": readings[0]["before"] == readings[1]["before"],
        "reset_restored_same_score": readings[0]["result"]["score"] == readings[1]["result"]["score"],
        "host_file_read_rejected": all(r["blocked_file_read"] for r in readings),
        "public_context_characters": [r["context_characters"] for r in readings],
        "custom_manifest_counts": dict(Counter(task["split"] for task in manifest["tasks"])),
        "official_split_membership_verified": True,
        "elapsed_seconds": real_monotonic() - started,
        "benchmark_data_or_solutions_in_report": False,
    }
    checks = ("official_scoring_observed", "public_api_execution_nonempty", "public_state_changed", "reset_restored_initial_public_state", "reset_restored_same_score", "host_file_read_rejected")
    results["completed"] = all(results[key] for key in checks)
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if results["completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
