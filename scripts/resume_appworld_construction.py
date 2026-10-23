"""Validate the specific pre-query AppWorld construction failure for source reuse.

This script never makes a model request. Its CLI prints a reviewable evidence
anchor or validates recovery inputs; the credential launcher starts a new run.
Original evidence is always read-only. This is intentionally not a general
checkpoint resumer and never selects records by source or query performance.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))

from agent_construction_lab._io import canonical_json, digest
from agent_construction_lab.fixed_model.client import ModelConfig, aggregate_usage
from agent_construction_lab.fixed_model.constructors import ConstructorConfig, ConstructorLibrary, candidate_module_sets, make_harness
from agent_construction_lab.fixed_model.spec import validate_manifest
from agent_construction_lab.fixed_model.study import SourceResumePayload, StudyConfig


SNAPSHOT_FILES = frozenset({"constructor_library.json", "episodes.jsonl", "failed_operations.jsonl",
    "model_config.json", "model_usage.jsonl", "run_provenance.json", "run_status.json",
    "source_pairs.jsonl", "study_config.json", "task_manifest.json"})
ALLOWED_RUNTIME_CHANGES = frozenset({"agent_construction_lab/fixed_model/constructors.py",
                                   "agent_construction_lab/fixed_model/study.py"})
CONSTRUCTION_ERROR = "Invalid fixed-model construction response"
EXTERNAL_CALLS = 5
EXTERNAL_TOKENS = 9526
GLOBAL_CALLS = 1000
GLOBAL_TOKENS = 12_000_000


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _lines(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _snapshot(source_run):
    source = Path(source_run)
    _require(source.is_dir() and not source.is_symlink(), "Source run must be an ordinary existing directory")
    _require({p.name for p in source.iterdir()} == SNAPSHOT_FILES,
             "Source run must contain exactly the failed source-only evidence set; support/query or other files are forbidden")
    result = {}
    for name in sorted(SNAPSHOT_FILES):
        path = source / name
        _require(path.is_file() and not path.is_symlink(), "Source evidence must contain ordinary files only")
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def snapshot_anchor(source_run):
    """Content anchor to record before preparing or launching a continuation."""
    return digest(_snapshot(source_run))


def _source_hashes():
    return {p.relative_to(REPOSITORY).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted((REPOSITORY / "agent_construction_lab").rglob("*.py"))}


def _function_source(text, name):
    node = next((n for n in ast.parse(text).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name), None)
    _require(node is not None, "Source-generating function is missing")
    return ast.get_source_segment(text, node)


def _constant_value(text, name):
    for node in ast.parse(text).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("Source-generating constant is missing: " + name)


def _source_generation_hash(provenance, current_hashes):
    path = "agent_construction_lab/fixed_model/constructors.py"
    current = (REPOSITORY / path).read_text(encoding="utf-8")
    if current_hashes[path] == provenance["source_sha256"][path]:
        original = current
    else:
        revision = provenance.get("code_revision")
        _require(isinstance(revision, str) and re.fullmatch(r"[0-9a-f]{40}", revision),
                 "An exact original Git revision is required to verify source-generating functions")
        result = subprocess.run(["git", "show", f"{revision}:{path}"], cwd=REPOSITORY, capture_output=True)
        _require(result.returncode == 0 and hashlib.sha256(result.stdout).hexdigest() == provenance["source_sha256"][path],
                 "Original constructor source does not match provenance")
        original = result.stdout.decode("utf-8")
    # A file-level exemption must never permit changed source interventions.
    for name in ("make_harness", "candidate_module_sets"):
        _require(_function_source(original, name) == _function_source(current, name),
                 "Source-generating function changed: " + name)
    for name in ("BASE_PROMPT", "MODULES"):
        _require(canonical_json(_constant_value(original, name)) == canonical_json(_constant_value(current, name)),
                 "Source execution guidance changed: " + name)
    _require(list(_constant_value(original, "MODULES")) == list(_constant_value(current, "MODULES")),
             "Source module ordering changed")
    return hashlib.sha256(_function_source(current, "make_harness").encode("utf-8")).hexdigest()


def _validate_source_records(manifest, config, episodes, pairs, usage):
    tasks = [task for task in manifest["tasks"] if task["split"] == "source"]
    combinations = candidate_module_sets(config.max_modules)
    _require(len(tasks) == 3 and len(config.source_seeds) == 1 and len(combinations) == 11,
             "Recovery requires the exact three-task, eleven-intervention source design")
    _require(len(episodes) == len(pairs) == 33 and len(usage) == 397,
             "Recovery requires all 33 source episodes/pairs and all 397 historical attempts")
    ledger = {row["attempt_id"]: row for row in usage}
    _require(list(ledger) == [f"attempt-{i:08d}" for i in range(1, 398)], "Historical attempt sequence is incomplete or duplicated")
    expected_pairs = []
    index = 0
    for task in tasks:
        for seed in config.source_seeds:
            baseline = episodes[index]
            for modules in combinations:
                row = episodes[index]
                condition = "source_intervention" if modules else "source_baseline"
                harness = make_harness(modules)
                expected = dict(episode_id=f"episode-{index:07d}", stage="source", condition=condition,
                    domain=task["domain_id"], task_id=task["task_id"], execution_seed=seed,
                    construction_seed=None, phase="source:" + condition, role="source",
                    harness=harness, harness_sha256=digest(harness))
                _require(all(row.get(key) == value for key, value in expected.items()),
                         "Source episode task/seed/harness/order does not match the frozen design")
                _require(row.get("valid_task_outcome") is True
                    and row.get("failure_category") != "infrastructure" and not row.get("infrastructure_error")
                    and type(row.get("success")) is bool and type(row.get("score")) in (int, float)
                    and math.isfinite(row["score"]) and 0 <= row["score"] <= 1,
                    "Every imported source episode must have a valid task outcome")
                attempts = row.get("attempt_ids", [])
                _require(attempts and all(a in ledger for a in attempts), "Source episode is missing physical attempts")
                physical = [ledger[a] for a in attempts]
                _require(all(p.get("phase") == expected["phase"] and p.get("role") == "source"
                    and p.get("task_id") == task["task_id"] and p.get("domain") == task["domain_id"]
                    and p.get("request_seed") == seed for p in physical), "Source attempt metadata mismatch")
                _require(digest(row["usage"]) == digest(aggregate_usage(physical)), "Source episode usage differs from its physical ledger")
                expected_pairs.append(dict(task_id=task["task_id"], domain_id=task["domain_id"], split="source", role="source",
                    seed=seed, baseline_seed=seed, features=manifest["domains"][task["domain_id"]]["features"], modules=list(modules),
                    baseline_success=baseline["success"], success=row["success"], baseline_score=baseline["score"], score=row["score"],
                    baseline_cost=baseline["usage"]["total_tokens"], cost=row["usage"]["total_tokens"],
                    baseline_episode_id=baseline["episode_id"], episode_id=row["episode_id"]))
                index += 1
    _require(digest(pairs) == digest(expected_pairs), "Source pairs are incomplete or disagree with matched source episodes")


def prepare_resume(source_run, manifest, model, config, *, anchor, expected_source_hashes=None):
    """Read and validate source-only evidence; return a runtime-bound payload.

    ``expected_source_hashes`` optionally pins the exact approved current package
    snapshot in addition to the mandatory two-file change restriction.
    No function here creates output, reads credentials or contacts a provider.
    """
    source = Path(source_run).resolve()
    snapshot = _snapshot(source_run)
    _require(isinstance(anchor, str) and anchor == digest(snapshot), "Original evidence anchor mismatch")
    manifest = validate_manifest(manifest)
    _require(manifest["benchmark"] == "appworld", "Source recovery is restricted to the live AppWorld pilot")
    original_manifest = _json(source / "task_manifest.json")
    original_model = _json(source / "model_config.json")
    original_config = _json(source / "study_config.json")
    _require(digest(manifest) == digest(original_manifest), "Task manifest changed after source collection")
    _require(digest(asdict(config)) == digest(original_config), "Study configuration changed after source collection")
    _require(set(config.methods) == {"G", "D", "S", "V", "F", "G+R", "F+R"} and not config.ablations,
             "Recovery must construct all seven original conditions")
    current_model = asdict(model)
    changed = {key for key in current_model if current_model[key] != original_model.get(key)}
    _require(changed == {"max_total_tokens"} and set(current_model) == set(original_model),
             "Only the declared token cap may change in the fixed model configuration")
    _require(original_model["max_total_tokens"] == 5_000_000 - EXTERNAL_TOKENS
        and current_model["max_total_tokens"] == GLOBAL_TOKENS - EXTERNAL_TOKENS
        and current_model["max_model_calls"] == GLOBAL_CALLS - EXTERNAL_CALLS
        and current_model["max_output_tokens"] == 2048, "Recovery spending limits must include calibration and preflight")
    status = _json(source / "run_status.json")
    _require(status.get("completed") is False and status.get("mock_model") is False and status.get("stage") == "failed"
        and status.get("stop_reason") == "ConstructorResponseError" and status.get("error") == CONSTRUCTION_ERROR
        and status.get("stopped_for_budget") is False, "Recovery is limited to the confirmed malformed first D construction")
    provenance = _json(source / "run_provenance.json")
    _require(provenance.get("manifest_sha256") == digest(original_manifest)
        and provenance.get("model_config_sha256") == digest(original_model)
        and provenance.get("study_config_sha256") == digest(original_config), "Original provenance configuration hash mismatch")
    current_hashes = _source_hashes()
    original_hashes = provenance["source_sha256"]
    _require(set(current_hashes) == set(original_hashes), "Runtime file set differs from the original run")
    if expected_source_hashes is not None:
        _require(current_hashes == expected_source_hashes, "Current runtime differs from the explicitly approved source snapshot")
    changes = {name: {"before": original_hashes[name], "after": current_hashes[name]}
               for name in current_hashes if current_hashes[name] != original_hashes[name]}
    _require(set(changes) <= ALLOWED_RUNTIME_CHANGES, "Runtime changed outside the constructor fix and source import hook")
    function_hash = _source_generation_hash(provenance, current_hashes)
    episodes, pairs, usage, failures = (_lines(source / name) for name in
        ("episodes.jsonl", "source_pairs.jsonl", "model_usage.jsonl", "failed_operations.jsonl"))
    _validate_source_records(manifest, config, episodes, pairs, usage)
    expected_failure = dict(operation="interrupted_construction_or_reflection", attempt_ids=["attempt-00000397"],
                            error=CONSTRUCTION_ERROR, valid_task_outcome=False)
    _require(failures == [expected_failure], "Historical construction failure owner is not the exact expected failure")
    last = usage[-1]
    targets = sorted({t["domain_id"] for t in manifest["tasks"] if t["split"] == "query"})
    _require(last.get("phase") == "construction:D" and last.get("role") == "builder" and last.get("task_id") is None
        and last.get("domain") == targets[0] and last.get("request_seed") == config.construction_seeds[0],
        "Historical final attempt is not the first D construction")
    for row in usage:
        _require(row.get("request_sent") is True and row.get("success") is True and row.get("error_type") is None
            and row.get("usage_consistent") is True and row.get("request_model") == model.model
            and row.get("response_model") == model.expected_response_model
            and row.get("expected_response_model") == model.expected_response_model
            and row.get("base_url") == model.base_url,
            "Historical attempt identity/transport/accounting is inconsistent")
        _require(all(type(row.get(key)) is int and row[key] >= 0 for key in ("input_tokens", "output_tokens", "total_tokens"))
            and row["total_tokens"] == row["input_tokens"] + row["output_tokens"], "Historical token usage must be complete")
    owners = Counter(a for row in episodes + failures for a in row["attempt_ids"])
    _require(set(owners) == {row["attempt_id"] for row in usage} and all(count == 1 for count in owners.values()),
             "Historical physical attempts are missing owners or are counted more than once")
    original_usage = aggregate_usage(usage)
    _require(digest(original_usage) == digest(status["usage"]), "Original run status and ledger totals disagree")
    library = ConstructorLibrary.from_source(pairs, ConstructorConfig(config.ridge_alpha, config.cost_weight, config.max_modules))
    _require(digest(library.to_dict()) == digest(_json(source / "constructor_library.json")), "Rebuilt constructor library differs from original model state")
    historical_failed = aggregate_usage([last])
    amendment = dict(schema_version=1, kind="source_only_construction_recovery", original_run=str(source),
        original_run_id=status["run_id"], source_snapshot_sha256=snapshot, source_anchor=anchor,
        source_data_sha256=library.source_data_sha256,
        imported_counts=dict(episodes=33, source_pairs=33, model_attempts=397, model_calls=397, failed_operations=1),
        allowed_runtime_changes=changes, make_harness_source_sha256=function_hash, original_usage=original_usage,
        historical_failed_usage=historical_failed,
        budget_amendment=dict(global_max_model_calls=GLOBAL_CALLS, global_max_total_tokens=GLOBAL_TOKENS,
            external_model_calls=EXTERNAL_CALLS, external_total_tokens=EXTERNAL_TOKENS,
            old_model_max_total_tokens=original_model["max_total_tokens"], new_model_max_total_tokens=model.max_total_tokens,
            model_max_calls=model.max_model_calls, max_output_tokens=model.max_output_tokens, peak_cost_upper_bound_usd=5.4432),
        interpretation="Source evidence is reused unchanged; all seven constructions, support searches and queries start anew. "
            "The old malformed construction call remains physical spend. No query outcomes existed when this amendment was made. "
            "External calibration/preflight spend is separate; the study ledger is cumulative and must not be summed with the original run.")
    payload = dict(bindings=dict(manifest=digest(manifest), model=digest(asdict(model)), study=digest(asdict(config))),
        source_run=str(source), snapshot_sha256=snapshot, runtime_sha256=current_hashes,
        episodes=episodes, pairs=pairs, usage=usage, failed_operations=failures,
        library_sha256=digest(library.to_dict()), amendment=amendment)
    _require(_snapshot(source) == snapshot, "Original evidence changed while recovery was being validated")
    return SourceResumePayload(canonical_json(payload), digest(payload))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--anchor-only", action="store_true")
    parser.add_argument("--anchor")
    parser.add_argument("--manifest")
    parser.add_argument("--model-config")
    parser.add_argument("--study-config")
    args = parser.parse_args(argv)
    if args.anchor_only:
        print(snapshot_anchor(args.source_run))
        return 0
    if not all((args.anchor, args.manifest, args.model_config, args.study_config)):
        parser.error("Validation requires --anchor, --manifest, --model-config and --study-config")
    payload = prepare_resume(args.source_run, _json(args.manifest), ModelConfig.from_dict(_json(args.model_config)),
                             StudyConfig.from_dict(_json(args.study_config)), anchor=args.anchor)
    amendment = json.loads(payload.payload_json)["amendment"]
    # Never print source traces, task instructions, provider IDs or credentials.
    print(json.dumps({"validated": True, "source_anchor": amendment["source_anchor"],
                      "imported_counts": amendment["imported_counts"], "budget_amendment": amendment["budget_amendment"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
