"""Run a configured AppWorld study with credentials supplied only over stdin/env."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_construction_lab._io import write_json, digest
from agent_construction_lab.fixed_model.__main__ import doctor
from agent_construction_lab.fixed_model.client import ModelConfig, OpenAICompatibleClient, UsageLedger
from agent_construction_lab.fixed_model.constructors import make_harness
from agent_construction_lab.fixed_model.environments import create_environment
from agent_construction_lab.fixed_model.executor import run_episode
from agent_construction_lab.fixed_model.study import StudyConfig, run_study


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-config", required=True)
    parser.add_argument("--appworld-root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--credentials-stdin", action="store_true")
    parser.add_argument("--manifest")
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--study-config")
    parser.add_argument("--calibration-task")
    parser.add_argument("--calibration-max-steps", type=int, default=4)
    parser.add_argument("--resume-from")
    parser.add_argument("--resume-anchor")
    args = parser.parse_args()
    if not 1 <= args.calibration_max_steps <= 100:
        parser.error("--calibration-max-steps must be between 1 and 100")
    if bool(args.resume_from) != bool(args.resume_anchor) or args.resume_from and args.calibration_task:
        parser.error("Source continuation requires --resume-from and --resume-anchor, without calibration")
    model = ModelConfig.from_dict(json.loads(Path(args.model_config).read_text(encoding="utf-8")))
    previous = os.environ.get(model.api_key_env) if model.api_key_env else None
    if args.credentials_stdin:
        if not model.api_key_env:
            parser.error("Credential input requires a named model.api_key_env")
        secret = json.load(sys.stdin)
        if (not isinstance(secret, dict) or set(secret) != {"api_key"}
                or not isinstance(secret["api_key"], str) or not secret["api_key"].strip()
                or any(char in secret["api_key"] for char in "\r\n")):
            raise ValueError("Invalid credential input")
        os.environ[model.api_key_env] = secret.pop("api_key")
    try:
        readiness = doctor(args.model_config, args.appworld_root)
        if not readiness["ready_for_live_smoke"]:
            print(json.dumps(readiness), flush=True)
            return 1
        if args.calibration_task:
            # Calibration is confined to official train and never chooses query tasks.
            from appworld import load_task_ids, update_root
            from uuid import uuid4
            update_root(args.appworld_root)
            if args.calibration_task not in set(load_task_ids("train")):
                raise ValueError("Calibration task must belong to official train")
            root = Path(args.out)
            root.mkdir(parents=True, exist_ok=False)
            ledger = UsageLedger(root / "model_usage.jsonl")
            env = create_environment("appworld", {"task_id": args.calibration_task},
                appworld_root=args.appworld_root, experiment_name="acl-calibration-" + uuid4().hex,
                seed=101, max_steps=args.calibration_max_steps, timeout_seconds=60, initialization_timeout_seconds=120)
            result = run_episode(env, OpenAICompatibleClient(model, ledger), make_harness(),
                task_id=args.calibration_task, domain="held_out_calibration", seed=101,
                max_steps=args.calibration_max_steps, max_context_chars=60000, phase="calibration", role="executor")
            write_json(root / "episode.json", result)
            write_json(root / "model_config.json", asdict(model))
            public = {key: result.get(key) for key in ("valid_task_outcome", "success", "score", "tool_steps", "failure_type",
                "execution_healthy", "finished", "actor_protocol", "finish_mode", "explicit_finish_count",
                "auto_final_count", "protocol_violation_count", "protocol_repairs_used", "protocol_repair_requests")}
            repository = Path(__file__).resolve().parents[1]
            public.update(task_id=args.calibration_task, max_steps=args.calibration_max_steps,
                model_config_sha256=digest(asdict(model)), source_sha256={
                    path.relative_to(repository).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in sorted((repository / "agent_construction_lab").rglob("*.py"))})
            public.update(usage=ledger.totals(), scope="Held-out train protocol calibration; not a query result")
            write_json(root / "calibration_summary.json", public)
            print(json.dumps(public), flush=True)
            return 0 if result["valid_task_outcome"] else 1
        if not args.manifest or not args.study_config:
            parser.error("Study runs require --manifest and --study-config")
        if Path(args.out).exists() and any(Path(args.out).iterdir()):
            raise ValueError("Output directory must be empty; existing provenance is never overwritten")
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        if args.manifest_sha256 and digest(manifest) != args.manifest_sha256:
            raise ValueError("Task manifest changed after selection was frozen")
        config = StudyConfig.from_dict(json.loads(Path(args.study_config).read_text(encoding="utf-8")))
        run_options = {}
        if args.resume_from:
            from scripts.resume_appworld_construction import prepare_resume
            run_options["source_resume"] = prepare_resume(args.resume_from, manifest, model, config,
                                                         anchor=args.resume_anchor)
        repository = Path(__file__).resolve().parents[1]
        provenance = {"manifest_sha256": digest(manifest), "model_config_sha256": digest(asdict(model)),
            "study_config_sha256": digest(asdict(config)), "source_sha256": {
                path.relative_to(repository).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted((repository / "agent_construction_lab").rglob("*.py"))}}
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True)
        provenance["code_revision"] = revision.stdout.strip() if revision.returncode == 0 else None
        print(json.dumps({"stage": "source_import" if args.resume_from else "source", "live_model": True}), flush=True)
        try:
            summary = run_study(manifest, model, args.out, config=config, appworld_root=args.appworld_root,
                                progress=lambda value: print(json.dumps(value), flush=True), **run_options)
        finally:
            if (Path(args.out) / "run_status.json").exists():
                write_json(Path(args.out) / "run_provenance.json", provenance)
        print(json.dumps({"completed": summary["completed"], "actual_episode_counts": summary["actual_episode_counts"],
                          "total_actual_usage": summary["total_actual_usage"]}), flush=True)
        return 0
    finally:
        if args.credentials_stdin:
            if previous is None:
                os.environ.pop(model.api_key_env, None)
            else:
                os.environ[model.api_key_env] = previous


if __name__ == "__main__":
    raise SystemExit(main())
