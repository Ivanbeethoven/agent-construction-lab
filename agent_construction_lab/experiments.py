"""Reconstructed simulator study with explicit source/support/query accounting."""
from collections import defaultdict
from dataclasses import asdict, dataclass
import csv
import random
from pathlib import Path
from .contracts import candidate_harnesses, public_features
from .fixtures import get_tasks
from .runtime import run_episode
from .learning import (PairedObservation, TrainingDataError, ConditionalUpliftModel, ScalarFinalValueModel, NearestGoodEndpoint)
from .search import SupportCase, optimize_support
from .artifacts import compile_artifact, run_artifact
from ._io import write_json, append_json


@dataclass
class PilotConfig:
    source_execution_seeds: tuple = (101, 211, 307)
    support_execution_seeds: tuple = (503, 509)
    query_execution_seeds: tuple = (1009, 1013, 1019)
    construction_seeds: tuple = (17, 29)
    support_per_family: int = 2
    candidate_budget: int = 6
    bootstrap_samples: int = 500
    bootstrap_seed: int = 2027
    split_seed: int = 43
    include_ablations: bool = True
    max_mechanisms: int = 2
    beam_width: int = 2
    ridge_alpha: float = 2.0
    cost_weight: float = 0.005
    uncertainty_penalty: float = 0.05

    def __post_init__(self):
        groups = [self.source_execution_seeds, self.support_execution_seeds, self.query_execution_seeds, self.construction_seeds]
        if any(set(a) & set(b) for i,a in enumerate(groups) for b in groups[i+1:]):
            raise ValueError("Seed channels must be disjoint")
        if min(self.support_per_family, self.candidate_budget, self.bootstrap_samples) < 1:
            raise ValueError("Budgets must be positive")

    @classmethod
    def from_value(cls, value=None):
        return value if isinstance(value, cls) else cls(**(value or {}))


class ExecutionLedger:
    def __init__(self):
        self.entries = []

    def record(self, result, phase, role, **metadata):
        row = dict(result.to_dict(), phase=phase, role=role, target_task_id=None)
        row.update(metadata)
        row["execution_id"] = f"execution-{len(self.entries):07d}"
        self.entries.append(row)
        return row["execution_id"]

    def totals(self):
        return dict(executions=len(self.entries), tool_steps=sum(r["tool_steps"] for r in self.entries),
                    cost_units=sum(r["cost_units"] for r in self.entries))


def collect_source_data(tasks=None, execution_seeds=(101,211,307), ledger=None, evaluator=run_episode):
    tasks = get_tasks("source") if tasks is None else tasks
    if not tasks or any(t.split != "source" for t in tasks):
        raise TrainingDataError("Source collection requires source tasks")
    ledger = ledger if ledger is not None else ExecutionLedger()
    records = []
    for task in tasks:
        for seed in execution_seeds:
            baseline = evaluator(task, (), seed=seed)
            baseline_id = ledger.record(baseline, "source", "source", split="source")
            for mechanisms in candidate_harnesses(task):
                result = evaluator(task, mechanisms, seed=seed) if mechanisms else baseline
                execution_id = ledger.record(result, "source", "source", split="source") if mechanisms else baseline_id
                records.append(PairedObservation(task.task_id, task.family, "source", "source", public_features(task),
                    mechanisms, seed, baseline.success, result.success, baseline.score, result.score,
                    baseline.tool_steps, result.tool_steps, baseline.cost_units, result.cost_units,
                    baseline.failure_type or "none", result.failure_type or "none", provenance=dict(
                        base_execution_id=baseline_id, candidate_execution_id=execution_id,
                        base_execution_seed=seed, candidate_execution_seed=seed)))
    return records


def partition_target_roles(tasks, support_per_family=2, seed=43):
    groups = defaultdict(list)
    for task in tasks:
        groups[task.family].append(task)
    support, query = {}, []
    rng = random.Random(seed)
    for family, group in sorted(groups.items()):
        eligible = [t for t in group if t.features.get("supports_idempotency") and t.features.get("persistent_storage")]
        if len(group) <= support_per_family or len(eligible) < support_per_family:
            raise ValueError("Insufficient support/query partition")
        chosen = rng.sample(eligible, support_per_family)
        support[family] = chosen
        ids = {t.task_id for t in chosen}
        query.extend(t for t in group if t.task_id not in ids)
    return support, query


def public_contract_rule(features, candidates):
    desired = []
    if features.get("transient_faults") in {"read", "write_after_commit"}:
        desired.append("retry")
    if features.get("transient_faults") == "write_after_commit":
        desired.append("idempotency")
    if features.get("restart_risk"):
        desired.append("checkpoint")
    if features.get("schema_variant") == "legacy":
        desired.append("schema_adapter")
    if features.get("budget", 30) <= 9 or len(desired) > 2:
        return ()
    selected = tuple(m for m in ("retry","idempotency","checkpoint","schema_adapter") if m in desired)
    return selected if selected in candidates else ()


def clustered_paired_bootstrap(rows, left, right, samples=500, seed=2027):
    def indexed(method):
        result = {}
        for row in rows:
            if row["method"] == method:
                key = (row["family"],row["task_id"],row["construction_seed"],row["execution_seed"])
                if key in result:
                    raise ValueError("Duplicate paired observation")
                result[key] = float(row["success"])
        return result
    a,b = indexed(left),indexed(right)
    if not a or a.keys() != b.keys():
        raise ValueError("Methods must have identical paired task/seed keys")
    groups = defaultdict(list)
    for key in a:
        groups[key[0]].append(a[key]-b[key])
    means = [sum(v)/len(v) for v in groups.values()]
    rng = random.Random(seed)
    draws = sorted(sum(rng.choice(means) for _ in means)/len(means) for _ in range(samples))
    return dict(left=left, right=right, difference=sum(means)/len(means), family_clusters=len(groups),
        paired_observations=len(a), ci_low=draws[int(.025*(samples-1))], ci_high=draws[int(.975*(samples-1))])


def positive_repair_control(tasks, ledger):
    matching = [t for t in tasks if t.features["transient_faults"] == "write_after_commit" and t.features["schema_variant"] == "standard"]
    support, query = matching[:2]
    support_seeds, query_seeds = (607,613), (1201,1213)
    plans = []
    for optimizer in ("greedy", "beam"):
        def evaluate(task, mechanisms, seed):
            result = run_episode(task, mechanisms, seed)
            ledger.record(result,"sanity_support","dev",method=optimizer)
            return result
        selected = optimize_support((), [SupportCase(support)], candidate_harnesses(support), optimizer=optimizer,
            candidate_budget=11, execution_seeds=support_seeds, evaluator=evaluate)
        plans.append((optimizer,selected))
    rows=[]
    for optimizer,selected in plans:
        scores=[]
        for mechanisms in ((),selected.selected_mechanisms):
            results=[run_episode(query,mechanisms,s) for s in query_seeds]
            for result in results:
                ledger.record(result,"sanity_query","dev",method=optimizer)
            scores.append(sum(r.success for r in results)/len(results))
        rows.append(dict(optimizer=optimizer,selected_mechanisms=list(selected.selected_mechanisms),
            candidates_evaluated=selected.candidates_evaluated,baseline_query_success_rate=scores[0],
            repaired_query_success_rate=scores[1],support_task_id=support.task_id,query_task_id=query.task_id))
    return dict(rows=rows,support_execution_seeds=support_seeds,query_execution_seeds=query_seeds)


def _csv(path, rows):
    if not rows:
        return
    with Path(path).open("w",newline="",encoding="utf-8") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run_pilot(out, config=None):
    config=PilotConfig.from_value(config)
    root=Path(out)
    if root.exists() and any(root.iterdir()):
        raise ValueError("Output directory must be empty")
    root.mkdir(parents=True,exist_ok=True)
    ledger=ExecutionLedger()
    records=collect_source_data(execution_seeds=config.source_execution_seeds,ledger=ledger)
    for row in records:
        append_json(root/"source_pairs.jsonl",row.to_dict())
    support,query=partition_target_roles(get_tasks("test"),config.support_per_family,config.split_seed)
    write_json(root/"splits.json",dict(source=[t.to_dict() for t in get_tasks("source")],
        target_support=[t.to_dict() for group in support.values() for t in group],target_query=[t.to_dict() for t in query],
        dev_not_used_for_training_or_tuning=[t.to_dict() for t in get_tasks("dev")]))
    endpoint=NearestGoodEndpoint.fit(records)
    scalar=ScalarFinalValueModel.fit(records,cost_weight=config.cost_weight)
    plans=[]
    for construction_seed in config.construction_seeds:
        model=ConditionalUpliftModel.fit(records,construction_seed=construction_seed,cost_weight=config.cost_weight)
        selectors={"generic":lambda f,c:(),"conditional_uplift":model.select,"scalar_final_value":scalar.select,
            "nearest_good_endpoint":endpoint.select,"public_contract_rule":public_contract_rule,
            "all_compatible":lambda f,c:max(c,key=len)}
        if config.include_ablations:
            for name,opts in (("uplift_no_failure",dict(include_failure=False)),("uplift_no_interactions",dict(include_interactions=False)),("uplift_no_uncertainty",dict(uncertainty_penalty=0))):
                selectors[name]=ConditionalUpliftModel.fit(records,**opts).select
        for task in query:
            candidates=candidate_harnesses(task)
            choices={name:fn(public_features(task),candidates) for name,fn in selectors.items()}
            for base in ("generic","conditional_uplift"):
                for optimizer in ("greedy","beam"):
                    method=f"{base}+{optimizer}_rsi"
                    def evaluate(spec,mechanisms,seed):
                        result=run_episode(spec,mechanisms,seed)
                        ledger.record(result,"support","support",method=method,target_task_id=task.task_id)
                        return result
                    search=optimize_support(choices[base],[SupportCase(t) for t in support[task.family]],candidates,
                        optimizer=optimizer,candidate_budget=config.candidate_budget,execution_seeds=config.support_execution_seeds,evaluator=evaluate)
                    choices[method]=search.selected_mechanisms
            for method,mechanisms in choices.items():
                relative=Path("artifacts")/str(construction_seed)/task.task_id/method
                artifact=compile_artifact(task,mechanisms,root/relative)
                entry=dict(task_id=task.task_id,method=method,construction_seed=construction_seed,
                    artifact_path=relative.as_posix(),manifest_sha256=artifact.manifest_sha256)
                append_json(root/"construction_log.jsonl",entry)
                plans.append((task,mechanisms,entry))
    rows=[]
    for task,mechanisms,plan in plans:
        for seed in config.query_execution_seeds:
            result=run_artifact(root/plan["artifact_path"],task,seed,plan["manifest_sha256"])
            ledger.record(result,"query","query",method=plan["method"],construction_seed=plan["construction_seed"])
            rows.append(dict(method=plan["method"],task_id=task.task_id,family=task.family,
                construction_seed=plan["construction_seed"],execution_seed=seed,success=result.success,
                score=result.score,tool_steps=result.tool_steps,cost_units=result.cost_units))
    methods=[]
    for name in sorted({r["method"] for r in rows}):
        selected=[r for r in rows if r["method"]==name]
        methods.append(dict(method=name,success_rate=sum(r["success"] for r in selected)/len(selected),
            mean_tool_steps=sum(r["tool_steps"] for r in selected)/len(selected),n=len(selected)))
    four=[]
    for optimizer in ("greedy","beam"):
        for construction in ("generic","conditional_uplift"):
            for active in (False,True):
                name=construction+(f"+{optimizer}_rsi" if active else "")
                four.append(dict(optimizer=optimizer,construction=construction,deployment_rsi=active,
                    success_rate=next(r["success_rate"] for r in methods if r["method"]==name)))
    comparisons=[clustered_paired_bootstrap(rows,m["method"],"generic",samples=config.bootstrap_samples) for m in methods if m["method"]!="generic"]
    control=positive_repair_control(get_tasks("dev"),ledger)
    summary=dict(schema_version=2,reconstructed=True,real_llm_evidence=False,config=asdict(config),methods=methods,four_cells=four,
        paired_comparisons=comparisons,positive_repair_control=control,accounting=dict(construction_environment_executions=0,
        artifacts_built=len(plans),all_physical_executions=ledger.totals()))
    for entry in ledger.entries:
        append_json(root/"execution_ledger.jsonl",entry)
    for row in rows:
        append_json(root/"query_results.jsonl",row)
    for name,data in (("query_results",rows),("method_summary",methods),("four_cells",four),("paired_comparisons",comparisons)):
        _csv(root/f"{name}.csv",data)
    write_json(root/"summary.json",summary)
    return summary
