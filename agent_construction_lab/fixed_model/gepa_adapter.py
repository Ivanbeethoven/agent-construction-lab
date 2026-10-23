"""Pinned official GEPA adapter with conservative whole-round budget reservation."""
from importlib import metadata
import math
from .._io import canonical_json
from .constructors import parse_json_response

GEPA_VERSION="0.1.4"


class MissingGepaDependencyError(RuntimeError):
    pass


class UnsupportedGepaVersionError(RuntimeError):
    pass


class _ReflectionAbort(BaseException):
    """Escape GEPA's internal Exception retries without repeating a model call."""
    def __init__(self, cause):
        self.cause=cause


def optimize_harness(initial, evaluator, reflector, *, max_evaluations, seed=0, max_modules=2):
    from .spec import validate_harness
    initial=validate_harness(initial)
    if type(max_modules) is not int or not 0<=max_modules<=2 or len(initial["modules"])>max_modules:
        raise ValueError("Initial harness exceeds the configured module bound")
    if type(max_evaluations) is not int or max_evaluations<1:
        raise ValueError("Positive evaluation budget required")
    try: installed=metadata.version("gepa")
    except metadata.PackageNotFoundError as error:
        raise MissingGepaDependencyError("Install the pinned dependency: pip install gepa==0.1.4") from error
    if installed!=GEPA_VERSION:
        raise UnsupportedGepaVersionError(f"Expected gepa=={GEPA_VERSION}; re-audit any different version")
    from gepa.optimize_anything import optimize_anything, GEPAConfig, EngineConfig, ReflectionConfig
    audit=dict(actual_evaluations=0,candidate_evaluation_requests=0,invalid_candidate_evaluations=0,
        reflection_calls=0,cache_evaluation=False)
    observations=[]

    def evaluate(candidate):
        if audit["candidate_evaluation_requests"]>=max_evaluations:
            raise RuntimeError("GEPA exceeded its reserved evaluation budget")
        audit["candidate_evaluation_requests"]+=1
        try:
            harness=validate_harness(parse_json_response(candidate))
            if len(harness["modules"])>max_modules: raise ValueError("Candidate exceeds configured module bound")
        except (ValueError,TypeError,KeyError):
            audit["invalid_candidate_evaluations"]+=1
            return -1e12,{"error":"Invalid harness JSON or candidate outside the bounded space"}
        value,feedback=evaluator(harness)
        if not math.isfinite(value): raise ValueError("Support score must be finite")
        audit["actual_evaluations"]+=1
        observations.append(dict(harness=harness,score=float(value)))
        return float(value),feedback

    def reflect(prompt):
        audit["reflection_calls"]+=1
        messages=[{"role":"user","content":prompt}] if isinstance(prompt,str) else prompt
        try:
            return reflector(messages)
        except Exception as error:
            raise _ReflectionAbort(error) from error

    # Seed costs one. Each proposed round can cost parent + child + full val.
    # Reserving all three prevents improvement-dependent overshoot.
    def insufficient_round_budget(state):
        return max_evaluations-audit["candidate_evaluation_requests"]<3

    try:
        result=optimize_anything(canonical_json(initial),evaluator=evaluate,
            objective=f"Improve support task execution while keeping exactly schema_version, system_prompt and modules; at most {max_modules} allowed guidance modules.",
            config=GEPAConfig(engine=EngineConfig(seed=seed,max_metric_calls=max_evaluations,
                parallel=False,cache_evaluation=False,raise_on_exception=True,display_progress_bar=False),
                reflection=ReflectionConfig(reflection_lm=reflect,reflection_minibatch_size=1,skip_perfect_score=False),
                stop_callbacks=[insufficient_round_budget]))
    except _ReflectionAbort as error:
        raise error.cause
    selected=validate_harness(parse_json_response(result.best_candidate))
    # Official selection uses full validation, not the latest parent remeasurement.
    selected_score=float(result.val_aggregate_scores[result.best_idx])
    audit.update(gepa_metric_calls=result.total_metric_calls,
        unused_evaluation_budget=max_evaluations-audit["candidate_evaluation_requests"])
    return dict(selected_harness=selected,selected_score=selected_score,audit=audit,official_gepa_version=installed)
