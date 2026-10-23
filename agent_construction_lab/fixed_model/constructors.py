"""Source-only bounded harness construction. Numerical predictors are not LLMs."""
import copy
from collections import defaultdict
from dataclasses import asdict, dataclass
from itertools import combinations
import json
import math
from .._io import canonical_json, digest
from ..learning import RidgeRegressor

BASE_PROMPT = "Complete the user's task using the documented public APIs. Execute code to change real state. Finish when done."
MODULES = {
    "plan": "Plan the required operations and identify the target state before executing.",
    "verify": "Read the changed state and verify that it matches the request before completing the task.",
    "tool_docs": "Consult public API documentation and required arguments before using unfamiliar tools.",
    "error_repair": "Inspect public tool errors, correct the action, and retry within the execution budget.",
}


class ConstructorDataError(ValueError):
    pass


class ConstructorResponseError(ValueError):
    pass


def candidate_module_sets(max_modules=2):
    if type(max_modules) is not int or not 0 <= max_modules <= 2:
        raise ValueError("At most two guidance modules are supported")
    return [group for n in range(max_modules+1) for group in combinations(MODULES, n)]


def make_harness(modules=(), system_prompt=BASE_PROMPT):
    from .spec import validate_harness
    result=validate_harness(dict(schema_version=1,system_prompt=system_prompt,modules=list(modules)))
    result["modules"]=[name for name in MODULES if name in result["modules"]]
    return result


def parse_json_response(text):
    text=text.strip()
    if text.startswith("```") and text.endswith("```"):
        text=text.split("\n",1)[1].rsplit("```",1)[0].strip()
    return json.loads(text)


def _public(value):
    if isinstance(value,dict):
        for key, item in value.items():
            if any(term in key.lower() for term in ("label","outcome","score","reward","solution","success","private")):
                raise ConstructorDataError("Outcome or private information in public features/profile")
            _public(item)
    elif isinstance(value,list):
        for item in value: _public(item)


@dataclass
class ConstructorConfig:
    ridge_alpha: float = 0.1
    cost_weight: float = 0.0
    max_modules: int = 2

    def __post_init__(self):
        candidate_module_sets(self.max_modules)
        if (type(self.ridge_alpha) not in (int,float) or not math.isfinite(self.ridge_alpha) or self.ridge_alpha <= 0
                or type(self.cost_weight) not in (int,float) or not math.isfinite(self.cost_weight) or self.cost_weight < 0):
            raise ValueError("Invalid numerical learner configuration")


class ConstructorLibrary:
    @classmethod
    def from_source(cls, records, config=None):
        self=cls()
        self.records=copy.deepcopy(records)
        self.config=config or ConstructorConfig()
        self.validate()
        self.source_data_sha256=digest(self.records)
        return self

    def validate(self):
        if not self.records:
            raise ConstructorDataError("Source data cannot be empty")
        groups=defaultdict(list)
        features={}
        observed=set()
        schema=None
        candidates=candidate_module_sets(self.config.max_modules)
        for row in self.records:
            if row.get("valid_task_outcome", True) is not True or row.get("infrastructure_error"):
                raise ConstructorDataError("Infrastructure failures cannot supply training labels")
            if row.get("split")!="source" or row.get("role","source")!="source":
                raise ConstructorDataError("Constructor training requires source role and split")
            if row.get("baseline_seed",row["seed"])!=row["seed"]:
                raise ConstructorDataError("Baseline and intervention seeds must match")
            _public(row["features"])
            if any(type(v) not in (int,float) or not math.isfinite(v) for v in row["features"].values()):
                raise ConstructorDataError("Features must be finite numeric values")
            keys=tuple(sorted(row["features"]))
            schema=keys if schema is None else schema
            if schema!=keys:
                raise ConstructorDataError("Feature schemas differ")
            domain=row["domain_id"]
            if domain in features and features[domain]!=row["features"]:
                raise ConstructorDataError("Features must be constant within a domain")
            features[domain]=row["features"]
            if tuple(row["modules"]) not in candidates:
                raise ConstructorDataError("Unsupported module candidate")
            for name in ("score","baseline_score"):
                if type(row[name]) not in (int,float) or not math.isfinite(row[name]):
                    raise ConstructorDataError("Scores must be finite")
            if self.config.cost_weight and (row["cost"] is None or row["baseline_cost"] is None):
                raise ConstructorDataError("Unknown usage requires zero cost weight")
            groups[(row["task_id"],row["seed"])].append(row)
            key=(row["task_id"],row["seed"],tuple(row["modules"]))
            if key in observed:
                raise ConstructorDataError("Duplicate paired candidate observation")
            observed.add(key)
            for name in ("cost","baseline_cost"):
                if row[name] is not None and (type(row[name]) not in (int,float) or not math.isfinite(row[name]) or row[name]<0):
                    raise ConstructorDataError("Known costs must be finite and nonnegative")
        for rows in groups.values():
            noops=[r for r in rows if not r["modules"]]
            if len(noops)!=1:
                raise ConstructorDataError("Exactly one real no-op observation is required per pair group")
            base=noops[0]
            for row in rows:
                for key in ("score","success","cost"):
                    if row["baseline_"+key]!=base[key] or base["baseline_"+key]!=base[key]:
                        raise ConstructorDataError("Inconsistent paired baseline")
        self.feature_names=list(schema)
        if hasattr(self,"source_data_sha256") and digest(self.records)!=self.source_data_sha256:
            raise ConstructorDataError("Source library mutated after fitting")

    def to_dict(self):
        return dict(records=copy.deepcopy(self.records),config=asdict(self.config),source_data_sha256=self.source_data_sha256)

    @classmethod
    def from_dict(cls, value):
        result=cls.from_source(value["records"],ConstructorConfig(**value["config"]))
        if result.source_data_sha256!=value["source_data_sha256"]:
            raise ConstructorDataError("Source data hash mismatch")
        return result


def _basis(features, modules, keys, ablation):
    context=[("bias",1.0)]
    if ablation!="no_conditions": context.extend((key,float(features[key])) for key in keys)
    names,values=[],[]
    for name,value in context:
        names.append(name);values.append(value)
    for module in MODULES:
        for name,value in context:
            names.append(f"{module}*{name}");values.append(float(module in modules)*value)
    if ablation!="no_interactions":
        for a,b in combinations(MODULES,2):
            for name,value in context:
                names.append(f"{a}*{b}*{name}");values.append(float(a in modules and b in modules)*value)
    return names,values


def construct_domain(condition, *, domain_id, public_profile, features, library, model_call=None, ablation=None, **kwargs):
    library.validate()
    _public(public_profile);_public(features)
    if sorted(features)!=library.feature_names:
        raise ConstructorDataError("Target feature schema differs from source schema")
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in features.values()):
        raise ConstructorDataError("Target features must be finite numeric values")
    if ablation not in (None,"no_conditions","no_interactions","no_negatives","no_nonpositive"):
        raise ValueError("Unknown ablation")
    candidates=candidate_module_sets(library.config.max_modules)
    candidate_library=[dict(candidate_id=f"c{i:02d}",modules=list(c)) for i,c in enumerate(candidates)]
    evidence=[]
    for source_domain in sorted({r["domain_id"] for r in library.records}):
        domain_rows=[r for r in library.records if r["domain_id"]==source_domain]
        for c in candidates:
            rows=[r for r in domain_rows if tuple(r["modules"])==c]
            evidence.append(dict(domain_id=source_domain,features=domain_rows[0]["features"],modules=list(c),observations=len(rows),
                mean_score=sum(r["score"] for r in rows)/len(rows) if rows else None,
                mean_paired_effect=sum(r["score"]-r["baseline_score"] for r in rows)/len(rows) if rows else None))
    metadata=dict(numeric_predictor_is_additional_LLM=False,target_environment_rollouts=0,query_outcomes_used=False,
        constructor_model_calls=0,candidate_library=candidate_library,source_evidence=evidence)
    if condition=="G":
        harness=make_harness(())
    elif condition in {"D","S"}:
        if model_call is None: raise ConstructorResponseError("A fixed-model builder callable is required")
        if len(canonical_json(evidence))>kwargs.get("source_summary_max_chars",60000):
            raise ConstructorDataError("Source evidence exceeds the declared summary budget; increase it explicitly rather than dropping source domains")
        instruction=(f"Return exactly a JSON harness with schema_version=1, system_prompt, modules (at most {library.config.max_modules})." if condition=="D" else
                     "Return exactly JSON with candidate_id from the candidate_library.")
        prompt=instruction+"\n"+canonical_json(dict(public_profile=public_profile,features=features,source_evidence=evidence,candidate_library=candidate_library))
        try:
            response=parse_json_response(model_call(prompt,"builder"))
            if condition=="D":
                from .spec import validate_harness
                harness=validate_harness(response)
                if len(harness["modules"])>library.config.max_modules:
                    raise ValueError("Builder exceeded the configured module bound")
            else:
                if not isinstance(response,dict) or set(response)!={"candidate_id"}: raise ValueError("Invalid selector response")
                selected=next(r for r in candidate_library if r["candidate_id"]==response["candidate_id"])
                harness=make_harness(selected["modules"])
                metadata["baseline_scope"]="Modular source-library selector; not a complete paper reproduction"
        except (ValueError,TypeError,KeyError,StopIteration) as error:
            raise ConstructorResponseError("Invalid fixed-model construction response") from error
        metadata["constructor_model_calls"]=1
    elif condition in {"F","V"}:
        rows=library.records
        if ablation=="no_negatives": rows=[r for r in rows if r["score"]>=r["baseline_score"]]
        if ablation=="no_nonpositive": rows=[r for r in rows if r["score"]>r["baseline_score"]]
        if not rows: raise ConstructorDataError("No observations remain after ablation")
        names=_basis(features,(),library.feature_names,ablation)[0]
        matrix=[_basis(r["features"],r["modules"],library.feature_names,ablation)[1] for r in rows]
        score=RidgeRegressor.fit(matrix,[r["score"]-(r["baseline_score"] if condition=="F" else 0) for r in rows],library.config.ridge_alpha)
        known=all(r["cost"] is not None and r["baseline_cost"] is not None for r in rows)
        cost=RidgeRegressor.fit(matrix,[r["cost"]-r["baseline_cost"] for r in rows],library.config.ridge_alpha) if known else None
        baseline=_basis(features,(),library.feature_names,ablation)[1]
        predictions=[]
        for c in candidates:
            x=_basis(features,c,library.feature_names,ablation)[1]
            effect=score.predict(x)-score.predict(baseline) if c else 0.0
            token_effect=cost.predict(x)-cost.predict(baseline) if cost else None
            predictions.append(dict(modules=list(c),predicted_score_effect=effect,predicted_token_effect=token_effect,
                objective=(effect if condition=="F" else score.predict(x))-library.config.cost_weight*(token_effect or 0)))
        selected=max(predictions,key=lambda p:p["objective"])
        harness=make_harness(selected["modules"])
        details=dict(feature_names=names,training_data_sha256=library.source_data_sha256,training_rows=len(rows),
            negative_score_effect_rows=sum(r["score"]<r["baseline_score"] for r in rows),
            zero_score_effect_rows=sum(r["score"]==r["baseline_score"] for r in rows),
            unknown_cost_rows=sum(r["cost"] is None or r["baseline_cost"] is None for r in rows),
            refitted_after_filtering=ablation in {"no_negatives","no_nonpositive"},ablation=ablation)
        metadata.update(predictions=predictions,numeric_learner=dict(score=score.to_dict(),cost=cost.to_dict() if cost else None,metadata=details))
    else:
        raise ValueError("Unknown construction condition")
    return dict(harness=harness,metadata=metadata,domain_id=domain_id,condition=condition)
