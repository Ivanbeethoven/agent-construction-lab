"""Bounded support-only greedy/beam search; every evaluation is accounted for."""
from dataclasses import dataclass, field
from .runtime import run_episode


@dataclass
class SupportCase:
    task: object
    role: str = "support"


@dataclass
class SearchResult:
    selected_mechanisms: tuple
    selected_score: float
    candidates_evaluated: int
    support_executions: int
    support_tool_steps: int
    evaluations: list = field(default_factory=list)


def optimize_support(initial, support, candidates, optimizer="greedy", candidate_budget=6,
                     execution_seeds=(503,509), evaluator=run_episode, cost_weight=0.005, beam_width=2, **kwargs):
    if not support or any(case.role != "support" for case in support):
        raise ValueError("Only explicitly support-role tasks may optimize")
    if optimizer not in {"greedy", "beam"} or candidate_budget < 1:
        raise ValueError("Invalid optimizer or budget")
    initial = tuple(initial)
    candidates = [tuple(c) for c in candidates]
    if initial not in candidates:
        raise ValueError("Initial candidate outside search space")
    seen, archive = set(), []
    executions = steps = 0

    def evaluate(candidate):
        nonlocal executions, steps
        results = [evaluator(case.task, candidate, seed=seed) for case in support for seed in execution_seeds]
        executions += len(results)
        steps += sum(r.tool_steps for r in results)
        score = sum(r.score - cost_weight*r.tool_steps for r in results)/len(results)
        archive.append({"mechanisms": candidate, "score": score})
        seen.add(candidate)

    evaluate(initial)
    while len(seen) < min(candidate_budget, len(candidates)):
        leaders = sorted(archive, key=lambda r:r["score"], reverse=True)[:beam_width if optimizer == "beam" else 1]
        neighbors = [c for c in candidates if c not in seen and any(len(set(c)^set(row["mechanisms"])) == 1 for row in leaders)]
        if not neighbors:
            if optimizer == "greedy":
                break
            neighbors = [c for c in candidates if c not in seen]
        # Breadth-first exploration preserves synergistic pairs in the beam archive.
        evaluate(neighbors[0])
    best = max(archive, key=lambda r:r["score"])
    return SearchResult(best["mechanisms"], best["score"], len(seen), executions, steps, archive)
