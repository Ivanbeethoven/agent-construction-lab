"""Seven-condition study with one query barrier and physical-call ownership."""
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version
import json
import math
from pathlib import Path
import random
from uuid import uuid4
from .._io import append_json, write_json, digest, canonical_json
from .constructors import ConstructorConfig, ConstructorLibrary, construct_domain, candidate_module_sets, make_harness
from .client import OpenAICompatibleClient, UsageLedger, ModelCallError, aggregate_usage
from .environments import create_environment, validate_appworld_membership
from .executor import run_episode
from .gepa_adapter import optimize_harness
from .spec import validate_manifest, freeze_harness, load_frozen_harness


class StudyExecutionError(RuntimeError):
    pass


@dataclass
class StudyConfig:
    source_seeds: tuple = (101,)
    execution_seeds: tuple = (101,)
    support_seeds: tuple | None = None
    construction_seeds: tuple = (17,)
    rsi_budgets: tuple = (4,)
    methods: tuple = ("G","D","S","V","F","G+R","F+R")
    ablations: tuple = ()
    max_steps: int = 12
    max_context_chars: int = 60000
    max_modules: int = 2
    bootstrap_samples: int = 500
    ridge_alpha: float = 2.0
    cost_weight: float = 0.0
    require_complete_token_usage: bool = True
    source_summary_max_chars: int = 24000
    support_feedback_max_chars: int = 24000
    appworld_timeout_seconds: float = 60
    appworld_initialization_timeout_seconds: float = 120
    remote_environment_url: str | None = None

    def __post_init__(self):
        candidate_module_sets(self.max_modules)
        for field in ("max_steps","max_context_chars","bootstrap_samples","source_summary_max_chars","support_feedback_max_chars"):
            value=getattr(self,field)
            if type(value) is not int or value<1: raise ValueError(f"{field} must be a positive integer")
        for field in ("ridge_alpha","cost_weight","appworld_timeout_seconds","appworld_initialization_timeout_seconds"):
            value=getattr(self,field)
            if type(value) not in (int,float) or not math.isfinite(value) or value<0 or (field!="cost_weight" and value==0):
                raise ValueError(f"{field} must be finite and within its valid range")
        if type(self.require_complete_token_usage) is not bool: raise ValueError("Usage requirement must be boolean")
        if not self.methods or len(set(self.methods))!=len(self.methods) or set(self.methods)-{"G","D","S","V","F","G+R","F+R"}:
            raise ValueError("Invalid study methods")
        if self.remote_environment_url:
            raise ValueError("Remote environment protocol is not implemented")
        axes=(self.source_seeds,self.execution_seeds,self.construction_seeds,self.rsi_budgets)
        if self.support_seeds is not None: axes=axes+(self.support_seeds,)
        for values in axes:
            if not values or any(type(v) is not int or v<0 for v in values) or len(set(values))!=len(values):
                raise ValueError("Nonempty unique nonnegative integer seed/budget axes required")
        if min(self.rsi_budgets)<1 or self.max_steps<1 or self.bootstrap_samples<1:
            raise ValueError("Budgets must be positive")
        if len(set(self.ablations))!=len(self.ablations) or set(self.ablations)-{"no_conditions","no_interactions","no_negatives","no_nonpositive"}:
            raise ValueError("Unknown F ablation")

    @classmethod
    def from_dict(cls, value): return cls(**value)


def _domain_means(rows):
    grouped=defaultdict(list)
    for row in rows: grouped[row["domain"]].append(float(row["success"]))
    return {key:sum(values)/len(values) for key,values in grouped.items()}


def _interval(values, samples, seed=2027):
    rng=random.Random(seed)
    draws=sorted(sum(rng.choice(values) for _ in values)/len(values) for _ in range(samples))
    return [draws[int(.025*(samples-1))],draws[int(.975*(samples-1))]]


def summarize(queries, episodes, constructions, usage, config, mock=False):
    keys={}
    for condition in {r["condition"] for r in queries}:
        selected=[r for r in queries if r["condition"]==condition]
        observations=[(r["domain"],r["task_id"],r["construction_seed"],r["execution_seed"]) for r in selected]
        if len(observations)!=len(set(observations)): raise StudyExecutionError("Duplicate query keys")
        keys[condition]=set(observations)
    if not keys or any(value!=next(iter(keys.values())) for value in keys.values()):
        raise StudyExecutionError("Conditions have different paired task/seed keys")
    source_ids={a for r in episodes if r["stage"]=="source" for a in r["attempt_ids"]}
    source_usage=aggregate_usage([r for r in usage if r["attempt_id"] in source_ids])
    methods={}
    domain_means={}
    for condition in sorted(keys):
        selected=[r for r in queries if r["condition"]==condition]
        domain_means[condition]=_domain_means(selected)
        means=list(domain_means[condition].values())
        ids={a for r in episodes if r["stage"]!="source" and r["condition"]==condition for a in r["attempt_ids"]}
        builds={condition}
        if "+R@" in condition: builds.add(condition.split("+R@")[0])
        ids.update(a for r in constructions if r["condition"] in builds for a in r["attempt_ids"])
        measured=aggregate_usage([r for r in usage if r["attempt_id"] in ids])
        include=condition not in {"G"} and not condition.startswith("G+R")
        total=measured["total_tokens"]
        if include:
            total=total+source_usage["total_tokens"] if total is not None and source_usage["total_tokens"] is not None else None
        methods[condition]=dict(episodes=len(selected),tasks=len({r["task_id"] for r in selected}),domains=len(means),
            domain_equal_success=sum(means)/len(means),domain_bootstrap_95ci=_interval(means,config.bootstrap_samples),
            insufficient_domains_for_strong_inference=len(means)<10,mean_tool_steps=sum(r["tool_steps"] for r in selected)/len(selected),
            failure_types=dict(Counter(r["failure_type"] for r in selected if r.get("failure_type"))),
            measured_method_usage=measured,source_cost_included=include,cold_total_tokens_including_source=total,
            source_amortized_total_tokens_per_query_episode=total/len(selected) if total is not None else None)
    comparisons={}
    names=sorted(keys)
    for i,left in enumerate(names):
        for right in names[i+1:]:
            differences=[domain_means[left][d]-domain_means[right][d] for d in domain_means[left]]
            comparisons[f"{left} - {right}"]=dict(mean_difference=sum(differences)/len(differences),
                domain_bootstrap_95ci=_interval(differences,config.bootstrap_samples),domains=len(differences))
    dependencies={}
    for name in ("gepa","appworld"):
        try: dependencies[name]=version(name)
        except PackageNotFoundError: dependencies[name]=None
    return dict(schema_version=2,reconstructed=True,completed=True,mock_model=mock,
        validation_scope="MOCK MODEL: interface integration only; scores are not research evidence" if mock else "Live-model study; interpretation is limited by the registered protocol",
        dependency_versions=dependencies,methods=methods,paired_differences=comparisons,
        total_actual_usage=aggregate_usage(usage),source_usage=source_usage,
        actual_episode_counts=dict(Counter(r["stage"] for r in episodes)),source_pairs=sum(r["stage"]=="source" for r in episodes),
        frozen_harnesses=len(constructions),accounting_note="Per-method source costs are counterfactual allocations; do not sum them as physical experiment spend.",
        inference_note="Resample domains, not seed repetitions. Non-significant residual gains do not establish equivalence.")


def run_study(manifest, model, out, *, config=None, mock=False, progress=None, appworld_root=None):
    config=config or StudyConfig()
    manifest=validate_manifest(manifest)
    if manifest["benchmark"]=="builtin" and not mock:
        raise ValueError("Builtin fixtures must be explicitly marked mock=True")
    if not mock:
        for field in ("model","model_snapshot","expected_response_model"):
            value=getattr(model,field)
            if not value or "REPLACE" in value.upper() or "mock" in value.lower():
                raise ValueError(f"Live studies require a concrete fixed {field}")
    if manifest["benchmark"]=="appworld": validate_appworld_membership(manifest,appworld_root)
    if mock and manifest["benchmark"]!="builtin": raise ValueError("Scripted smoke is limited to builtin fixtures")
    root=Path(out)
    if root.exists() and any(root.iterdir()): raise ValueError("Output directory must be empty; existing evidence is never overwritten")
    root.mkdir(parents=True,exist_ok=True)
    run_id=uuid4().hex
    usage=UsageLedger(root/"model_usage.jsonl")
    client=OpenAICompatibleClient(model,usage)
    episodes=[]; constructions=[]; queries=[]; pairs=[]; plans=[]
    write_json(root/"task_manifest.json",manifest)
    write_json(root/"model_config.json",asdict(model))
    write_json(root/"study_config.json",asdict(config))
    write_json(root/"run_status.json",dict(completed=False,stage="source",mock_model=mock,run_id=run_id))

    def check_usage(attempt_ids):
        rows=[r for r in usage.snapshot() if r["attempt_id"] in set(attempt_ids)]
        if config.require_complete_token_usage and any(r.get(k) is None for r in rows
                for k in ("input_tokens","output_tokens","total_tokens")):
            raise StudyExecutionError("Incomplete token usage; strict accounting requires every physical call")

    def episode(task,harness,stage,condition,seed,construction_seed=None):
        environment=create_environment(manifest["benchmark"],task,appworld_root=appworld_root,
            experiment_name=f"acl-{run_id}-{len(episodes)}",seed=seed,max_steps=config.max_steps,
            timeout_seconds=config.appworld_timeout_seconds,initialization_timeout_seconds=config.appworld_initialization_timeout_seconds)
        row=run_episode(environment,client,harness,task_id=task["task_id"],domain=task["domain_id"],
            seed=seed,max_steps=config.max_steps,max_context_chars=config.max_context_chars,
            phase=f"{stage}:{condition}",role="source" if stage=="source" else "executor")
        row.update(episode_id=f"episode-{len(episodes):07d}",stage=stage,condition=condition,domain=task["domain_id"],
            task_id=task["task_id"],execution_seed=seed,construction_seed=construction_seed,
            harness=harness,harness_sha256=digest(harness))
        episodes.append(row);append_json(root/"episodes.jsonl",row)
        if not row["valid_task_outcome"]:
            raise StudyExecutionError("Infrastructure failed; no failure label is admitted into training or evaluation")
        check_usage(row["attempt_ids"])
        return row

    def freeze(condition,domain,construction_seed,harness,metadata,attempt_ids):
        relative=Path("artifacts")/str(construction_seed)/digest(domain)[:12]/condition.replace("@","-")
        metadata=dict(metadata,model_config_sha256=digest(asdict(model)),study_config_sha256=digest(asdict(config)),
            domain_profile_sha256=digest(manifest["domains"][domain]),source_data_sha256=library.source_data_sha256,
            mock_model=mock,reconstructed=True)
        info=freeze_harness(harness,root/relative,metadata)
        entry=dict(path=relative.as_posix(),manifest_sha256=info["manifest_sha256"],harness_sha256=digest(harness),
            condition=condition,domain=domain,construction_seed=construction_seed,attempt_ids=attempt_ids,metadata=metadata)
        constructions.append(entry);append_json(root/"constructions.jsonl",entry)
        return entry

    try:
        for task in (t for t in manifest["tasks"] if t["split"]=="source"):
            for seed in config.source_seeds:
                baseline=episode(task,make_harness(()),"source","source_baseline",seed)
                for modules in candidate_module_sets(config.max_modules):
                    result=episode(task,make_harness(modules),"source","source_intervention",seed) if modules else baseline
                    row=dict(task_id=task["task_id"],domain_id=task["domain_id"],split="source",role="source",seed=seed,baseline_seed=seed,
                        features=manifest["domains"][task["domain_id"]]["features"],modules=list(modules),
                        baseline_success=baseline["success"],success=result["success"],baseline_score=baseline["score"],score=result["score"],
                        baseline_cost=baseline["usage"]["total_tokens"],cost=result["usage"]["total_tokens"],
                        baseline_episode_id=baseline["episode_id"],episode_id=result["episode_id"])
                    pairs.append(row);append_json(root/"source_pairs.jsonl",row)
        library=ConstructorLibrary.from_source(pairs,ConstructorConfig(config.ridge_alpha,config.cost_weight,config.max_modules))
        write_json(root/"constructor_library.json",library.to_dict())
        targets=sorted({t["domain_id"] for t in manifest["tasks"] if t["split"]=="query"})
        if progress: progress(dict(stage="construction"))
        for construction_seed in config.construction_seeds:
            for domain in targets:
                base_results={}
                required=set(config.methods)-{"G+R","F+R"}
                required.update(c.split("+")[0] for c in config.methods if "+R" in c)
                if config.ablations: required.add("F")
                for condition in sorted(required):
                    attempts=[]
                    def model_call(prompt,role):
                        completed=client.complete([{"role":"user","content":prompt}],phase=f"construction:{condition}",role=role,domain=domain,task_id=None,seed=construction_seed)
                        attempts.extend(completed.attempt_ids)
                        check_usage(completed.attempt_ids)
                        return completed.content
                    settings=manifest["domains"][domain]
                    built=construct_domain(condition,domain_id=domain,public_profile=settings["profile"],features=settings["features"],
                        library=library,model_call=model_call,source_summary_max_chars=config.source_summary_max_chars)
                    entry=freeze(condition,domain,construction_seed,built["harness"],built["metadata"],attempts)
                    base_results[condition]=built["harness"]
                    if condition in config.methods: plans.append(entry)
                for ablation in config.ablations:
                    built=construct_domain("F",domain_id=domain,public_profile=settings["profile"],features=settings["features"],library=library,ablation=ablation)
                    plans.append(freeze(f"F-{ablation}",domain,construction_seed,built["harness"],built["metadata"],[]))
                for method in (m for m in config.methods if "+R" in m):
                    for budget in config.rsi_budgets:
                        condition=f"{method}@{budget}";attempts=[]
                        def evaluate(harness):
                            results=[episode(task,harness,"support",condition,seed,construction_seed)
                                for task in manifest["tasks"] if task["split"]=="support" and task["domain_id"]==domain
                                for seed in (config.support_seeds if config.support_seeds is not None else config.execution_seeds)]
                            costs=[r["usage"]["total_tokens"] for r in results]
                            if config.cost_weight and any(c is None for c in costs): raise StudyExecutionError("Unknown support usage")
                            score=sum(r["score"]-config.cost_weight*(c or 0) for r,c in zip(results,costs))/len(results)
                            # Only public trajectory events and aggregate support scores reach reflection.
                            feedback=[dict(score=r["score"],trace=[e for e in r["trace"] if e.get("event")!="terminal_evaluation"]) for r in results]
                            return score,dict(support_episodes=len(results),public_trace_excerpt=canonical_json(feedback)[:config.support_feedback_max_chars])
                        def reflect(messages):
                            completed=client.complete(messages,phase=f"support:{condition}:reflection",role="optimizer",domain=domain,task_id=None,seed=construction_seed)
                            attempts.extend(completed.attempt_ids);return completed.content
                        def checked_reflect(messages):
                            text=reflect(messages)
                            check_usage(attempts)
                            return text
                        optimized=optimize_harness(base_results[method.split("+")[0]],evaluate,checked_reflect,
                            max_evaluations=budget,seed=construction_seed,max_modules=config.max_modules)
                        append_json(root/"support_searches.jsonl",dict(optimized,condition=condition,domain=domain,construction_seed=construction_seed))
                        plans.append(freeze(condition,domain,construction_seed,optimized["selected_harness"],optimized["audit"],attempts))
        write_json(root/"query_lock.json",dict(all_constructions_frozen_before_query=True,plans=plans))
        for plan in plans: load_frozen_harness(root/plan["path"],expected_manifest_hash=plan["manifest_sha256"])
        if progress: progress(dict(stage="query"))
        for plan in plans:
            harness=load_frozen_harness(root/plan["path"],expected_manifest_hash=plan["manifest_sha256"])
            for task in manifest["tasks"]:
                if task["split"]=="query" and task["domain_id"]==plan["domain"]:
                    for seed in config.execution_seeds:
                        row=episode(task,harness,"query",plan["condition"],seed,plan["construction_seed"])
                        queries.append(row);append_json(root/"query_results.jsonl",row)
        # Each physical attempt must have exactly one episode or construction owner.
        owned=Counter(a for row in episodes+constructions for a in row["attempt_ids"])
        if set(owned)!={r["attempt_id"] for r in usage.snapshot()} or any(n!=1 for n in owned.values()):
            raise StudyExecutionError("Physical attempt ownership is incomplete or duplicated")
        summary=summarize(queries,episodes,constructions,usage.snapshot(),config,mock)
        write_json(root/"summary.json",summary)
        write_json(root/"run_status.json",dict(completed=True,stage="complete",usage=usage.totals(),mock_model=mock,run_id=run_id,
            spending_limits=usage.budget_status(model)))
        return summary
    except Exception as error:
        # Failed builder/reflection calls never produce a frozen harness. Give
        # their attempts an explicit failure owner instead of inventing one.
        owned={a for row in episodes+constructions for a in row["attempt_ids"]}
        orphaned=[row for row in usage.snapshot() if row["attempt_id"] not in owned]
        if orphaned:
            append_json(root/"failed_operations.jsonl",dict(operation="interrupted_construction_or_reflection",
                attempt_ids=[row["attempt_id"] for row in orphaned],error=str(error),valid_task_outcome=False))
        blocked=[row for row in usage.snapshot() if row.get("budget_blocked") or row.get("error_type") in
            {"budget_usage_unknown","token_reservation_exceeded"}]
        write_json(root/"run_status.json",dict(completed=False,stage="failed",error=str(error),usage=usage.totals(),mock_model=mock,run_id=run_id,
            spending_limits=usage.budget_status(model),stopped_for_budget=bool(blocked),
            stop_reason=blocked[-1]["error_type"] if blocked else type(error).__name__))
        if isinstance(error,StudyExecutionError): raise
        raise StudyExecutionError(f"Study aborted ({type(error).__name__}); no synthetic fallback or replacement result") from error
