"""Verify a completed local AppWorld run and export only public metric fields."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_construction_lab._io import digest, write_json
from agent_construction_lab.fixed_model.__main__ import verify_run
from agent_construction_lab.fixed_model.client import ModelConfig, _usage, aggregate_usage
from agent_construction_lab.fixed_model.spec import load_frozen_harness, validate_manifest
from agent_construction_lab.fixed_model.study import StudyConfig, summarize
from scripts.resume_appworld_construction import _source_generation_hash

REPOSITORY = Path(__file__).resolve().parents[1]
HASH = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
FINISH_COUNTS = ("explicit_finish_count", "auto_final_count", "protocol_violation_count", "protocol_repairs_used", "protocol_repair_requests")
FINISH_FIELDS = ("finish_mode", *FINISH_COUNTS)
QUERY_FIELDS = ("task_id", "domain", "condition", "construction_seed", "execution_seed", "success",
                "score", "tool_steps", "failure_type", "harness_sha256", "valid_task_outcome", "execution_healthy", "finished",
                "finish_audit_available", *FINISH_FIELDS)
FAILURES = {None, "task_not_solved", "step_budget_exhausted", "context_budget_exhausted", "invalid_action"}
RESUME_FILES = {"task_manifest.json", "model_config.json", "study_config.json", "run_provenance.json",
                "run_status.json", "episodes.jsonl", "source_pairs.jsonl", "constructor_library.json",
                "model_usage.jsonl", "failed_operations.jsonl"}
RESUME_RUNTIME = {"agent_construction_lab/fixed_model/constructors.py", "agent_construction_lab/fixed_model/study.py"}
MODEL_FIELDS = ("model", "model_snapshot", "expected_response_model", "temperature", "top_p", "reasoning_effort",
                "max_output_tokens", "max_tokens_parameter", "send_seed", "use_tools", "retries", "timeout",
                "input_price_per_million", "output_price_per_million", "cached_input_price_per_million",
                "pricing_version", "max_model_calls", "max_total_tokens", "request_token_reserve",
                "actor_protocol", "actor_protocol_max_repairs")
LIMITATIONS = [
    "本结果仅为真实模型与 AppWorld 的小规模工程试验；不是官方排行榜成绩。",
    "费用由配置费率和提供方 token 用量估算，不是官方账单；预检及校准费用另列。",
    "DeepSeek 返回的模型别名不证明底层权重不可变，文档版本声明不等同于快照证明。",
    "send_seed=False 时种子仅作实验配对索引，不保证模型输出确定性。",
    "同一域和构造种子下使用相同 harness 的条件，其执行成绩差异不能归因为方法增益。",
    "任务成功是 AppWorld 官方终态评分的结果；成功不等于执行健康或已显式结束。",
    "公开导出不包含任务原文、轨迹、提示、模拟账户密码或私有评估细节。",
]


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_lines(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def check_hash(value):
    if not isinstance(value, str) or not HASH.fullmatch(value):
        raise ValueError("Invalid SHA-256 identifier")
    return value


def check_identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("Unsafe public identifier")
    return value


def number(value, *, nullable=False):
    if nullable and value is None:
        return value
    if type(value) not in {int, float} or not math.isfinite(value):
        raise ValueError("Metric must be finite numeric data")
    return value


def public_manifest_fields(manifest):
    """No free-form profile or description is copied, even from a public file."""
    tasks = []
    for task in manifest["tasks"]:
        row = {field: check_identifier(task[field]) for field in ("task_id", "generator_id", "domain_id", "split", "benchmark_split")}
        if "public_spec_sha256" in task:
            row["public_spec_sha256"] = check_hash(task["public_spec_sha256"])
        tasks.append(row)
    domains = {check_identifier(name): {"features": {check_identifier(key): number(value)
               for key, value in domain["features"].items()}} for name, domain in manifest["domains"].items()}
    selected = manifest.get("selection", {})
    selection = {}
    if "seed" in selected:
        selection["seed"] = number(selected["seed"])
    if "version" in selected:
        selection["version"] = check_identifier(selected["version"])
    for field in ("instruction_content_used_for_selection", "held_out_instructions_read", "official_test_lists_read"):
        if field in selected:
            if type(selected[field]) is not bool:
                raise ValueError("Selection flags must be boolean")
            selection[field] = selected[field]
    if "excluded_calibration_generators" in selected:
        selection["excluded_calibration_generators"] = [check_identifier(value) for value in selected["excluded_calibration_generators"]]
    return {"schema_version": 1, "benchmark": "appworld", "official_protocol": manifest.get("official_protocol") is True,
            "group_disjoint_support_query": manifest.get("group_disjoint_support_query") is True,
            "domains": domains, "tasks": tasks, "selection": selection}


def _task_keys(manifest):
    return {(t["task_id"], t["domain_id"], t["generator_id"], t["split"], t["benchmark_split"], t.get("public_spec_sha256"))
            for t in manifest["tasks"]}


def _public_usage(values):
    fields = ("attempts", "model_calls", "failed_attempts", "input_tokens", "output_tokens", "total_tokens",
              "cached_input_tokens", "reasoning_tokens", "cost_usd", "latency_seconds")
    return {key: number(values[key], nullable=True) for key in fields if key in values}


def _execution_health(rows, *, episode_key="query_episodes"):
    """Keep task scoring separate from execution and termination status."""
    for row in rows:
        for field in ("success", "valid_task_outcome", "execution_healthy", "finished"):
            if type(row.get(field)) is not bool:
                raise ValueError("Execution health flags must be explicit booleans")
        if row.get("failure_type") not in FAILURES:
            raise ValueError("Unknown execution failure category")
    clean = lambda row: row["execution_healthy"] and row["finished"]
    return {episode_key: len(rows), "task_successes": sum(row["success"] for row in rows),
        **{field: {"true": sum(row[field] for row in rows), "false": sum(not row[field] for row in rows)}
           for field in ("valid_task_outcome", "execution_healthy", "finished")},
        "clean_finishes": sum(clean(row) for row in rows),
        "failure_types": dict(sorted(Counter(row.get("failure_type") or "none" for row in rows).items())),
        "success_with_execution_issue": sum(row["success"] and not clean(row) for row in rows)}


def _finish_fields(row, model, *, ledger=None, physical_calls=None):
    """Only publish bounded enums/counts; old missing audit data stays unknown."""
    present = {field for field in FINISH_FIELDS if field in row}
    if row.get("actor_protocol", model.actor_protocol) != model.actor_protocol:
        raise ValueError("Episode actor protocol differs from model configuration")
    if not present and model.actor_protocol == "v1":
        return {"finish_audit_available": False, **{field: None for field in FINISH_FIELDS}}
    if present != set(FINISH_FIELDS):
        raise ValueError("Incomplete finish protocol audit")
    mode = row["finish_mode"]
    if mode is not None and (not isinstance(mode, str) or mode not in {"explicit_tool", "explicit_json"}):
        raise ValueError("Unknown public finish mode")
    counts = {field: row[field] for field in FINISH_COUNTS}
    if any(type(value) is not int or value < 0 for value in counts.values()):
        raise ValueError("Finish protocol counters must be nonnegative integers")
    if (counts["auto_final_count"] != 0 or counts["explicit_finish_count"] != int(mode is not None)
            or type(row.get("finished")) is not bool or row["finished"] != (mode is not None)
            or counts["protocol_repairs_used"] > (model.actor_protocol_max_repairs if model.actor_protocol != "v1" else 0)
            or counts["protocol_violation_count"] < counts["protocol_repairs_used"]
            or counts["protocol_repair_requests"] < counts["protocol_repairs_used"]
            or (counts["protocol_repairs_used"] == 0 and counts["protocol_repair_requests"] != 0)
            or model.actor_protocol == "v2_required_tools" and mode == "explicit_json"):
        raise ValueError("Finish protocol audit is inconsistent")
    if ledger is not None:
        repair_ids = row.get("protocol_repair_attempt_ids")
        if (not isinstance(repair_ids, list) or any(not isinstance(value, str) for value in repair_ids)
                or len(set(repair_ids)) != len(repair_ids) or not set(repair_ids) <= set(row["attempt_ids"])):
            raise ValueError("Protocol repair attempts must belong uniquely to their episode")
        actual = sum(ledger[value].get("request_sent") is True for value in repair_ids)
        if counts["protocol_repair_requests"] != actual:
            raise ValueError("Protocol repair count differs from physical requests")
        physical_calls = sum(ledger[value].get("request_sent") is True for value in row["attempt_ids"])
    if physical_calls is not None and counts["protocol_repair_requests"] > physical_calls:
        raise ValueError("Protocol repair requests exceed the episode request count")
    if physical_calls is not None and counts["protocol_violation_count"] + counts["explicit_finish_count"] > physical_calls:
        raise ValueError("Protocol outcomes exceed the episode request count")
    return {"finish_audit_available": True, "finish_mode": mode, **counts}


def _finish_summary(audits):
    known = [row for row in audits if row["finish_audit_available"]]
    unknown = len(audits) - len(known)
    totals = {field: sum(row[field] for row in known) for field in FINISH_COUNTS}
    return {"audited_episodes": len(known), "unknown_legacy_episodes": unknown,
        "finish_modes": dict(sorted(Counter(row["finish_mode"] or "unfinished" for row in known).items())),
        "known_counts": totals, **{key: None if unknown else value for key, value in totals.items()}}


def _manifest_design(manifest):
    result = {}
    domains = set()
    for stage in ("source", "support", "query"):
        tasks = [task for task in manifest["tasks"] if task["split"] == stage]
        selected_domains = {task["domain_id"] for task in tasks}
        domains.update(selected_domains)
        result[stage] = {"tasks": len(tasks), "generators": len({task["generator_id"] for task in tasks}),
                         "domains": len(selected_domains), "public_feature_vectors": len({
                             digest(manifest["domains"][name]["features"]) for name in selected_domains})}
    result["registered_public_feature_vectors"] = len({digest(manifest["domains"][name]["features"]) for name in domains})
    result["constant_public_features"] = result["registered_public_feature_vectors"] == 1
    return result


def _limitations(design):
    source, query = design["source"], design["query"]
    limitations = list(LIMITATIONS)
    limitations.append(f"Source 包含 {source['generators']} 个 generator、{source['tasks']} 个任务、{source['domains']} 个域和 "
                       f"{source['public_feature_vectors']} 种公开特征向量；generator 数量不等于独立特征条件的重复。")
    if design["constant_public_features"]:
        limitations.append("所有已注册 source/support/query 域使用同一公开特征向量；此设计不能证明 F 的条件学习或迁移收益。")
    elif source["public_feature_vectors"] <= 1:
        limitations.append("Source 仅有一种公开特征条件，无法据此识别条件学习效应；迁移收益需要独立域验证。")
    else:
        limitations.append("公开特征与 generator 数量本身不建立条件学习或迁移收益；需要独立域上的对照验证。")
    limitations.append(f"Query 包含 {query['domains']} 个目标域、{query['generators']} 个 generator、{query['tasks']} 个任务；"
                       + ("域数量不足以支持强统计推断；单域 bootstrap 区间不能解释为显著性证据。"
                          if query["domains"] < 10 else "推断范围仍受域抽样、独立重复和预注册设计限制。"))
    return limitations


def _harness_equivalence_groups(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["domain"], row["construction_seed"], row["harness_sha256"])].append(row)
    return [{"domain": domain, "construction_seed": seed, "harness_sha256": harness,
             "conditions": sorted({row["condition"] for row in members}),
             "query_episodes": len(members), "shared_across_conditions": len({row["condition"] for row in members}) > 1}
            for (domain, seed, harness), members in sorted(grouped.items())]


def _integer(value):
    if type(value) is not int or value < 0:
        raise ValueError("Resume counts and limits must be nonnegative integers")
    return value


def _verify_resume(run, local, model_raw, config_raw, provenance, episodes, usage, failures):
    """Validate source-only recovery against the untouched original run.

    This does not trust a copied aggregate or add an imported ledger twice.
    Local provenance paths are used only for verification and never published.
    """
    path = run / "resume_amendment.json"
    if not path.exists():
        if failures:
            raise ValueError("Historical failure owners require a source-resume amendment")
        return None
    raw = read_json(path)
    if raw.get("schema_version") != 1 or raw.get("kind") != "source_only_construction_recovery":
        raise ValueError("Unsupported resume amendment")
    original = Path(raw["original_run"]).resolve()
    if original == run or original.is_relative_to(run) or run.is_relative_to(original):
        raise ValueError("Resume original run must be separate from the completed run")
    snapshot = raw.get("source_snapshot_sha256", {})
    if set(snapshot) != RESUME_FILES or {p.name for p in original.iterdir()} != set(snapshot):
        raise ValueError("Original source snapshot file set differs")
    for name, expected in snapshot.items():
        source = original / name
        if source.is_symlink() or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != check_hash(expected):
            raise ValueError("Original source snapshot hash differs")
    if raw.get("source_anchor") != digest(snapshot):
        raise ValueError("Original source anchor differs")
    original_status = read_json(original / "run_status.json")
    if (original_status.get("completed") is not False or original_status.get("mock_model") is not False
            or original_status.get("stage") != "failed" or original_status.get("stop_reason") != "ConstructorResponseError"
            or original_status.get("stopped_for_budget") is not False):
        raise ValueError("Recovery origin must be a failed live source-only run")
    if raw.get("original_run_id") != original_status.get("run_id"):
        raise ValueError("Original run ID differs")
    old_provenance = read_json(original / "run_provenance.json")
    old_model = read_json(original / "model_config.json")
    old_manifest = read_json(original / "task_manifest.json")
    old_study = read_json(original / "study_config.json")
    for field, value in (("manifest_sha256", old_manifest), ("model_config_sha256", old_model), ("study_config_sha256", old_study)):
        if old_provenance.get(field) != digest(value):
            raise ValueError("Original run config provenance differs")
    if old_manifest != local or old_study != config_raw:
        raise ValueError("Resume changed manifest or study configuration")
    if {k: v for k, v in old_model.items() if k != "max_total_tokens"} != {k: v for k, v in model_raw.items() if k != "max_total_tokens"}:
        raise ValueError("Resume changed a model field other than the token limit")
    old_sources, new_sources = old_provenance.get("source_sha256", {}), provenance["source_sha256"]
    if set(old_sources) != set(new_sources):
        raise ValueError("Resume changed the runtime file set")
    changes = {name: {"before": check_hash(old_sources[name]), "after": new_sources[name]}
               for name in old_sources if old_sources[name] != new_sources[name]}
    if not set(changes) <= RESUME_RUNTIME or raw.get("allowed_runtime_changes") != changes:
        raise ValueError("Resume runtime changes exceed the declared amendment")
    if raw.get("make_harness_source_sha256") != _source_generation_hash(old_provenance, new_sources):
        raise ValueError("Resume source harness factory changed")

    old_episodes = read_lines(original / "episodes.jsonl")
    old_usage = read_lines(original / "model_usage.jsonl")
    old_pairs = read_lines(original / "source_pairs.jsonl")
    old_failures = read_lines(original / "failed_operations.jsonl")
    if not old_episodes or any(row.get("stage") != "source" for row in old_episodes):
        raise ValueError("Recovery origin is not source-only")
    if episodes[:len(old_episodes)] != old_episodes or usage[:len(old_usage)] != old_usage:
        raise ValueError("Imported episode or attempt prefix differs from original evidence")
    if any(row.get("stage") == "source" for row in episodes[len(old_episodes):]):
        raise ValueError("Resumed study reran or duplicated source episodes")
    if failures != old_failures or read_lines(run / "source_pairs.jsonl") != old_pairs:
        raise ValueError("Imported source pairs or historical failure owners differ")
    if read_json(run / "constructor_library.json") != read_json(original / "constructor_library.json"):
        raise ValueError("Imported source library differs")
    if raw.get("source_data_sha256") != digest(old_pairs) or read_json(run / "constructor_library.json").get("source_data_sha256") != digest(old_pairs):
        raise ValueError("Resume source data anchor differs from imported source pairs")
    owners = Counter(a for row in old_episodes + old_failures for a in row["attempt_ids"])
    old_ids = [row["attempt_id"] for row in old_usage]
    if len(set(old_ids)) != len(old_ids) or set(owners) != set(old_ids) or any(n != 1 for n in owners.values()):
        raise ValueError("Original attempt ownership is missing or duplicated")
    if any(row.get("valid_task_outcome") is not False or not row.get("attempt_ids")
           or row.get("operation") != "interrupted_construction_or_reflection" for row in old_failures):
        raise ValueError("Historical failed operation cannot be a task outcome")
    historical_ids = {a for row in old_failures for a in row["attempt_ids"]}
    historical = aggregate_usage([row for row in old_usage if row["attempt_id"] in historical_ids])
    original_usage = aggregate_usage(old_usage)
    counts = {"episodes": len(old_episodes), "source_pairs": len(old_pairs), "model_attempts": len(old_usage),
              "model_calls": original_usage["model_calls"], "failed_operations": len(old_failures)}
    if raw.get("imported_counts") != counts or raw.get("original_usage") != original_usage or original_status.get("usage") != original_usage:
        raise ValueError("Resume original counts or usage disagree with physical evidence")
    if raw.get("historical_failed_usage") != historical:
        raise ValueError("Resume historical failure usage differs")
    budget = raw.get("budget_amendment", {})
    budget_fields = ("global_max_model_calls", "global_max_total_tokens", "external_model_calls", "external_total_tokens",
                     "old_model_max_total_tokens", "new_model_max_total_tokens", "model_max_calls", "max_output_tokens")
    safe_budget = {key: _integer(budget[key]) for key in budget_fields}
    if (budget["old_model_max_total_tokens"] != old_model.get("max_total_tokens")
            or budget["new_model_max_total_tokens"] != model_raw.get("max_total_tokens")
            or budget["model_max_calls"] != model_raw.get("max_model_calls")
            or budget["max_output_tokens"] != model_raw.get("max_output_tokens")
            or budget["new_model_max_total_tokens"] <= budget["old_model_max_total_tokens"]
            or budget["global_max_model_calls"] != budget["model_max_calls"] + budget["external_model_calls"]
            or budget["global_max_total_tokens"] != budget["new_model_max_total_tokens"] + budget["external_total_tokens"]):
        raise ValueError("Resume budget amendment differs from model limits")
    all_usage = aggregate_usage(usage)
    if all_usage["model_calls"] > budget["model_max_calls"] or all_usage["total_tokens"] is None or all_usage["total_tokens"] > budget["new_model_max_total_tokens"]:
        raise ValueError("Resumed study exceeded its cumulative budget")
    safe_budget["peak_cost_upper_bound_usd"] = number(budget["peak_cost_upper_bound_usd"])
    if safe_budget["peak_cost_upper_bound_usd"] < 0:
        raise ValueError("Resume peak cost cannot be negative")
    safe = {"schema_version": 1, "kind": raw["kind"], "original_run_id": check_identifier(raw["original_run_id"]),
        "recovery_reason": "constructor_response_validation_failed_before_support_and_query",
        "source_snapshot_sha256": snapshot, "source_anchor": check_hash(raw["source_anchor"]),
        "source_data_sha256": check_hash(raw["source_data_sha256"]), "imported_counts": counts,
        "allowed_runtime_changes": changes, "make_harness_source_sha256": check_hash(raw["make_harness_source_sha256"]),
        "original_usage": _public_usage(original_usage), "historical_failed_usage": _public_usage(historical),
        "budget_amendment": safe_budget,
        "original_config_sha256": {key: old_provenance[key] for key in ("manifest_sha256", "model_config_sha256", "study_config_sha256")}}
    return {"raw": raw, "public": safe, "historical": historical, "original": original_usage,
            "new": aggregate_usage(usage[len(old_usage):])}


def _extras(model, environment_report, preflight_report, calibration_summary):
    extras = {}
    if environment_report is not None:
        raw = read_json(environment_report)
        if raw.get("completed") is not True or raw.get("model_calls") != 0:
            raise ValueError("Environment-only validation must be complete and have zero model calls")
        result = {"model_calls": 0, "completed": True}
        for key in ("fresh_worlds", "elapsed_seconds", "initial_score"):
            if key in raw:
                result[key] = number(raw[key])
        for key in ("official_scoring_observed", "public_api_execution_nonempty", "public_state_changed",
                    "reset_restored_initial_public_state", "reset_restored_same_score", "host_file_read_rejected"):
            if key in raw:
                if type(raw[key]) is not bool:
                    raise ValueError("Adapter validation flags must be boolean")
                result[key] = raw[key]
        extras["environment_validation"] = result
    if preflight_report is not None:
        raw = read_json(preflight_report)
        if raw.get("requested_model") != model.model or raw.get("response_model") != model.expected_response_model:
            raise ValueError("Preflight identity differs from study model")
        normalized = _usage({"usage": raw.get("usage")}, model)
        if not normalized["usage_consistent"] or normalized["total_tokens"] is None:
            raise ValueError("Preflight usage is incomplete or inconsistent")
        extras["preflight"] = {"model_calls": 1, "usage": _public_usage(normalized),
                               "cost_basis": "study_configured_rates_applied_to_preflight_usage"}
    if calibration_summary is not None:
        raw = read_json(calibration_summary)
        if raw.get("valid_task_outcome") is not True or type(raw.get("success")) is not bool:
            raise ValueError("Calibration has no valid task outcome")
        usage = raw.get("usage", {})
        normalized = _usage({"usage": {"prompt_tokens": usage.get("input_tokens"),
                         "completion_tokens": usage.get("output_tokens"), "total_tokens": usage.get("total_tokens"),
                         "prompt_tokens_details": {"cached_tokens": usage.get("cached_input_tokens")}}}, model)
        if not normalized["usage_consistent"] or normalized["total_tokens"] is None:
            raise ValueError("Calibration usage is incomplete or inconsistent")
        if raw.get("failure_type") not in FAILURES:
            raise ValueError("Unknown calibration failure category")
        extras["calibration"] = {"success": raw["success"], "score": number(raw["score"]),
             "tool_steps": number(raw["tool_steps"]), "failure_type": raw.get("failure_type"),
             "model_calls": number(usage["model_calls"]), "usage": _public_usage(normalized),
             "cost_basis": "study_configured_rates_applied_to_calibration_usage"}
        extras["calibration"].update(_finish_fields(raw, model, physical_calls=_integer(usage["model_calls"])))
        extras["calibration"]["actor_protocol"] = model.actor_protocol
        for field in ("valid_task_outcome", "execution_healthy", "finished"):
            if field in raw:
                if type(raw[field]) is not bool:
                    raise ValueError("Calibration health flags must be boolean")
                extras["calibration"][field] = raw[field]
        if model.actor_protocol == "v2_required_tools" and not all(field in raw for field in ("execution_healthy", "finished")):
            raise ValueError("V2 calibration requires execution health flags")
    charged = [item for key, item in extras.items() if key in {"preflight", "calibration"}]
    extras["additional_spend"] = {"model_calls": sum(item["model_calls"] for item in charged),
        "total_tokens": sum(item["usage"]["total_tokens"] for item in charged),
        "cost_usd": sum(item["usage"]["cost_usd"] for item in charged)
            if all(item["usage"]["cost_usd"] is not None for item in charged) else None,
        "supplied_reports_only": True}
    return extras


def export_results(run, public_manifest, out, *, environment_report=None, preflight_report=None, calibration_summary=None):
    run, out = Path(run).resolve(), Path(out).resolve()
    # This is deliberately the first read. In-progress query outcomes are never
    # opened merely to decide whether a run is exportable.
    status = read_json(run / "run_status.json")
    if status.get("completed") is not True or status.get("mock_model") is not False:
        raise ValueError("Only completed, explicitly non-mock runs can be exported")
    if out == run or out.is_relative_to(run) or out.exists() and any(out.iterdir()):
        raise ValueError("Export requires a new or empty directory outside the local run")
    local = validate_manifest(read_json(run / "task_manifest.json"))
    public = validate_manifest(read_json(public_manifest))
    if local["benchmark"] != "appworld" or public["benchmark"] != "appworld":
        raise ValueError("Export is restricted to real AppWorld runs")
    provenance = read_json(run / "run_provenance.json")
    model_raw, config_raw = read_json(run / "model_config.json"), read_json(run / "study_config.json")
    for field, value in (("manifest_sha256", local), ("model_config_sha256", model_raw), ("study_config_sha256", config_raw)):
        if provenance.get(field) != digest(value):
            raise ValueError(f"Run provenance mismatch: {field}")
    if local.get("public_manifest_sha256") != digest(public) or _task_keys(local) != _task_keys(public):
        raise ValueError("Public/local manifest hash or task-role mapping differs")
    current_sources = {path.relative_to(REPOSITORY).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                       for path in sorted((REPOSITORY / "agent_construction_lab").rglob("*.py"))}
    if provenance.get("source_sha256") != current_sources:
        raise ValueError("Run source hashes differ from current code")
    revision = provenance.get("code_revision")
    if revision is not None and (not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40,64}", revision)):
        raise ValueError("Invalid code revision")
    model, config = ModelConfig.from_dict(model_raw), StudyConfig.from_dict(config_raw)
    if not model.expected_response_model or "mock" in model.model.lower():
        raise ValueError("Live model identity is not fixed")
    verification = verify_run(run)
    lock = read_json(run / "query_lock.json")
    constructions = read_lines(run / "constructions.jsonl")
    for row in constructions:
        path = (run / row["path"]).resolve()
        if not path.is_relative_to(run):
            raise ValueError("Construction path escapes the run")
        harness = load_frozen_harness(path, expected_manifest_hash=row["manifest_sha256"])
        if digest(harness) != row["harness_sha256"]:
            raise ValueError("Construction harness hash differs")
    plans = {(row["domain"], row["condition"], row["construction_seed"]): row for row in lock["plans"]}
    if len(plans) != len(lock["plans"]):
        raise ValueError("Duplicate frozen plan")
    if any(row not in constructions for row in lock["plans"]):
        raise ValueError("Frozen query plan lacks a construction record")

    # All validity/provenance checks above precede opening any query result.
    episodes, queries, usage = (read_lines(run / name) for name in ("episodes.jsonl", "query_results.jsonl", "model_usage.jsonl"))
    failures = read_lines(run / "failed_operations.jsonl") if (run / "failed_operations.jsonl").exists() else []
    resumed = _verify_resume(run, local, model_raw, config_raw, provenance, episodes, usage, failures)
    episode_ids = {row["episode_id"]: row for row in episodes}
    if len(episode_ids) != len(episodes):
        raise ValueError("Duplicate episode ID")
    attempts = [row["attempt_id"] for row in usage]
    owned = Counter(attempt for row in episodes + constructions + failures for attempt in row["attempt_ids"])
    if len(set(attempts)) != len(attempts) or set(owned) != set(attempts) or any(value != 1 for value in owned.values()):
        raise ValueError("Attempt ownership is missing or duplicated")
    for row in usage:
        if row.get("request_model") != model.model or row.get("expected_response_model") != model.expected_response_model:
            raise ValueError("Request identity changed during run")
        if row.get("success") and row.get("response_model") != model.expected_response_model:
            raise ValueError("Response identity changed during run")
        for field in ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens", "reasoning_tokens"):
            if row.get(field) is not None and (type(row[field]) is not int or row[field] < 0):
                raise ValueError("Invalid provider token count")
        if config.require_complete_token_usage and any(row.get(field) is None for field in ("input_tokens", "output_tokens", "total_tokens")):
            raise ValueError("Strict run contains unknown provider token usage")
        if all(row.get(field) is not None for field in ("input_tokens", "output_tokens", "total_tokens")):
            if row["input_tokens"] + row["output_tokens"] != row["total_tokens"]:
                raise ValueError("Provider total tokens disagree with input/output")
        if row.get("cached_input_tokens") is not None and row.get("input_tokens") is not None and row["cached_input_tokens"] > row["input_tokens"]:
            raise ValueError("Cache tokens exceed input tokens")
        if row.get("cost_usd") is not None and number(row["cost_usd"]) < 0:
            raise ValueError("Provider cost cannot be negative")
    task_roles = {task["task_id"]: (task["domain_id"], task["split"]) for task in local["tasks"]}
    usage_by_id = {row["attempt_id"]: row for row in usage}
    finish_audits = {}
    for row in episodes:
        if row.get("valid_task_outcome") is not True or row.get("infrastructure_error"):
            raise ValueError("Completed run contains an invalid task outcome")
        if row.get("stage") not in {"source", "support", "query"} or task_roles.get(row.get("task_id")) != (row.get("domain"), row.get("stage")):
            raise ValueError("Episode task/domain/stage differs from registered manifest")
        finish_audits[row["episode_id"]] = _finish_fields(row, model, ledger=usage_by_id)
    expected_conditions = {f"{method}@{budget}" for method in config.methods if "+R" in method for budget in config.rsi_budgets}
    expected_conditions.update(method for method in config.methods if "+R" not in method)
    expected_conditions.update("F-" + ablation for ablation in config.ablations)
    expected_keys = {(task["domain_id"], task["task_id"], seed, execution_seed)
                     for task in local["tasks"] if task["split"] == "query"
                     for seed in config.construction_seeds for execution_seed in config.execution_seeds}
    grouped = {condition: set() for condition in expected_conditions}
    safe_queries = []
    for row in queries:
        if row.get("condition") not in grouped or digest(row) != digest(episode_ids.get(row.get("episode_id"))):
            raise ValueError("Query row differs from its episode or registered condition")
        key = (row["domain"], row["task_id"], row["construction_seed"], row["execution_seed"])
        if key in grouped[row["condition"]]:
            raise ValueError("Duplicate query key")
        grouped[row["condition"]].add(key)
        plan = plans.get((row["domain"], row["condition"], row["construction_seed"]))
        if not plan or row["harness_sha256"] != plan["harness_sha256"] or digest(row["harness"]) != row["harness_sha256"]:
            raise ValueError("Query harness differs from its frozen plan")
        if row.get("stage") != "query" or type(row.get("success")) is not bool or row.get("failure_type") not in FAILURES:
            raise ValueError("Invalid public query outcome")
        for field in ("valid_task_outcome", "execution_healthy", "finished"):
            if type(row.get(field)) is not bool:
                raise ValueError("Query execution health flags must be explicit booleans")
        check_identifier(row["task_id"]); check_identifier(row["domain"]); check_hash(row["harness_sha256"])
        for field in ("score", "tool_steps", "construction_seed", "execution_seed"):
            number(row[field])
        safe_queries.append({field: finish_audits[row["episode_id"]][field] if field in {"finish_audit_available", *FINISH_FIELDS}
                             else row[field] for field in QUERY_FIELDS})
    if not expected_keys or any(keys != expected_keys for keys in grouped.values()):
        raise ValueError("Conditions are missing paired query tasks or seeds")
    if {row["episode_id"] for row in queries} != {row["episode_id"] for row in episodes if row["stage"] == "query"}:
        raise ValueError("Query episode coverage differs")
    health = {"overall": _execution_health(queries), "by_condition": {
        condition: _execution_health([row for row in queries if row["condition"] == condition])
        for condition in sorted(expected_conditions)}}
    health["overall"]["finish_audit"] = _finish_summary([finish_audits[row["episode_id"]] for row in queries])
    for condition, values in health["by_condition"].items():
        values["finish_audit"] = _finish_summary([finish_audits[row["episode_id"]] for row in queries if row["condition"] == condition])
    stage_health = {}
    for stage in ("source", "support", "query"):
        selected = [row for row in episodes if row["stage"] == stage]
        stage_health[stage] = _execution_health(selected, episode_key="episodes")
        stage_health[stage]["finish_audit"] = _finish_summary([finish_audits[row["episode_id"]] for row in selected])
    design = _manifest_design(public)
    limitations = _limitations(design)
    equivalence = _harness_equivalence_groups(queries)
    calculated = summarize(queries, episodes, constructions, usage, config, mock=False)
    saved = read_json(run / "summary.json")
    if saved.get("completed") is not True or saved.get("mock_model") is not False:
        raise ValueError("Summary is not a completed live result")
    for field in ("methods", "paired_differences", "total_actual_usage", "source_usage", "actual_episode_counts", "source_pairs", "frozen_harnesses"):
        if saved.get(field) != calculated[field]:
            raise ValueError(f"Recorded aggregate does not match physical evidence: {field}")
    if status.get("usage") != aggregate_usage(usage):
        raise ValueError("Run status usage differs from physical evidence")
    if resumed is not None:
        if saved.get("resume_amendment") != resumed["raw"] or saved.get("historical_failed_usage") != resumed["historical"]:
            raise ValueError("Summary resume amendment or historical failure usage differs")
    elif "resume_amendment" in saved or "historical_failed_usage" in saved:
        raise ValueError("Summary claims recovery without verified historical evidence")
    dependencies = {}
    for key in ("appworld", "gepa"):
        value = saved.get("dependency_versions", {}).get(key)
        if value is not None:
            if not isinstance(value, str) or not re.fullmatch(r"[0-9][A-Za-z0-9.+_-]*", value):
                raise ValueError("Invalid dependency version identifier")
            dependencies[key] = value
    methods = {}
    for condition, values in calculated["methods"].items():
        methods[condition] = {field: values[field] for field in ("episodes", "tasks", "domains", "domain_equal_success",
            "domain_bootstrap_95ci", "insufficient_domains_for_strong_inference", "mean_tool_steps", "failure_types",
            "source_cost_included", "cold_total_tokens_including_source", "source_amortized_total_tokens_per_query_episode")}
        methods[condition]["measured_method_usage"] = _public_usage(values["measured_method_usage"])
    report = {"schema_version": 1, "completed": True, "mock_model": False, "benchmark": "appworld",
        "scope": "small_engineering_pilot_not_statistical_evidence", "limitations": limitations,
        "verification": {"frozen_harnesses": verification["frozen_harnesses"], "paired_query_keys_match": True,
                         "physical_attempts_have_unique_owners": True, "source_hashes_match_current": True},
        "provenance": {field: provenance[field] for field in ("manifest_sha256", "model_config_sha256", "study_config_sha256", "code_revision", "source_sha256")},
        "public_manifest_sha256": digest(public), "public_manifest": public_manifest_fields(public),
        "model_config": {field: getattr(model, field) for field in MODEL_FIELDS}, "study_config": asdict(config),
        "dependency_versions": dependencies,
        "methods": methods, "paired_differences": calculated["paired_differences"],
        "harness_equivalence_groups": equivalence, "execution_health": health,
        "stage_execution_health": stage_health, "manifest_design": design,
        "total_actual_usage": _public_usage(calculated["total_actual_usage"]), "source_usage": _public_usage(calculated["source_usage"]),
        "actual_episode_counts": calculated["actual_episode_counts"],
        "extra_validation": _extras(model, environment_report, preflight_report, calibration_summary)}
    if resumed is not None:
        extra = report["extra_validation"]["additional_spend"]
        budget = resumed["public"]["budget_amendment"]
        if extra["model_calls"] != budget["external_model_calls"] or extra["total_tokens"] != budget["external_total_tokens"]:
            raise ValueError("Extra validation reports differ from the amended external budget accounting")
        report.update(resume_amendment=resumed["public"], historical_failed_usage=_public_usage(resumed["historical"]),
                      imported_original_usage=_public_usage(resumed["original"]), new_run_usage=_public_usage(resumed["new"]))
        report["verification"]["original_source_anchor_and_import_prefix_verified"] = True
    # Construct every output before creating the destination, so failed
    # verification does not leave an apparently publishable partial report.
    json.dumps(report, allow_nan=False)
    readme = ["# AppWorld 真实模型工程试验", "", "仅为小规模工程结果，不用于统计推断。", "",
              "| 条件 | 终态成功/执行数 | 域等权成功率 | 平均工具步数 | 健康且结束/执行数 |", "|---|---:|---:|---:|---:|"]
    for condition, values in sorted(methods.items()):
        won = sum(row["success"] for row in safe_queries if row["condition"] == condition)
        clean = health["by_condition"][condition]["clean_finishes"]
        readme.append(f"| {condition} | {won}/{values['episodes']} | {values['domain_equal_success']:.3f} | {values['mean_tool_steps']:.2f} | {clean}/{values['episodes']} |")
    overall = health["overall"]
    readme += ["", "任务成功由 AppWorld 官方终态评分判定，与执行健康和显式结束分别记录；成功不代表健康完成。",
        f"Query 共 {overall['query_episodes']} 次：有效终态评分 {overall['valid_task_outcome']['true']} 次，执行健康 {overall['execution_healthy']['true']} 次，显式结束 {overall['finished']['true']} 次。",
        f"终态成功 {overall['task_successes']} 次，其中 {overall['success_with_execution_issue']} 次伴有执行异常或未显式结束。",
        "执行结束类别：" + "、".join(f"{name}={count}" for name, count in overall["failure_types"].items()) + "。", ""]
    shared = [group for group in equivalence if group["shared_across_conditions"]]
    if shared:
        readme.append("以下条件在同一域和构造种子下使用完全相同的 harness；其执行成绩差异不能归因为方法增益：")
        readme.append("")
        for group in shared:
            readme.append(f"- {group['domain']}，构造种子 {group['construction_seed']}：" + "、".join(group["conditions"])
                          + f"（harness `{group['harness_sha256']}`）。")
    spend = report["total_actual_usage"]
    readme += ["", f"研究调用：{spend['model_calls']} 次；token：{spend['total_tokens']}；估算费用：{spend['cost_usd']} USD。",
               f"单列额外校验：{report['extra_validation']['additional_spend']['model_calls']} 次调用；详见 report.json。", ""]
    if resumed is not None:
        historical = report["historical_failed_usage"]
        readme += ["本次为首次构造失败后的显式续跑：完整复用原 source 记录，重新执行全部构造、support 和 query。",
            f"历史失败构造开销：{historical['model_calls']} 次调用、{historical['total_tokens']} token，已含在上述累计研究用量中，不能再次相加。",
            "该开销不作为方法成绩；构造提示及总 token 上限的修订和原证据锚点见 report.json。", ""]
    readme += ["各阶段健康与收尾统计（由实际 episode 记录重算；未记录的旧审计字段保留未知）：", "",
        "| 阶段 | 执行数 | 终态成功 | 健康且结束 | 明确 finish | 修复轮次 | 修复物理请求 | 未记录审计 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for stage, values in stage_health.items():
        audit = values["finish_audit"]
        display = lambda key: "未知" if audit[key] is None else str(audit[key])
        readme.append(f"| {stage} | {values['episodes']} | {values['task_successes']} | {values['clean_finishes']} | "
            f"{display('explicit_finish_count')} | {display('protocol_repairs_used')} | {display('protocol_repair_requests')} | {audit['unknown_legacy_episodes']} |")
    readme.append("")
    readme.extend("- " + limitation for limitation in limitations)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "report.json", report)
    with (out / "query_metrics.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=QUERY_FIELDS, lineterminator="\n")
        writer.writeheader(); writer.writerows(safe_queries)
    (out / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8", newline="\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--public-manifest", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--environment-report")
    parser.add_argument("--preflight-report")
    parser.add_argument("--calibration-summary")
    args = parser.parse_args(argv)
    report = export_results(args.run, args.public_manifest, args.out, environment_report=args.environment_report,
                            preflight_report=args.preflight_report, calibration_summary=args.calibration_summary)
    print(json.dumps({"exported": True, "out": args.out, "conditions": sorted(report["methods"])}))


if __name__ == "__main__":
    main()
