"""Read-only source/support diagnosis with an explicit public-output allowlist.

No model is called. Query rows are excluded before analysis and query_results,
query_lock and summary are never read. Prompts, trajectories, instructions,
answers, provider response bodies and simulated account values are never output.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent_construction_lab._io import canonical_json, digest, write_json
from agent_construction_lab.fixed_model.constructors import ConstructorLibrary, _basis, construct_domain, make_harness
from agent_construction_lab.fixed_model.spec import validate_harness, validate_manifest, RUNTIME_FILES
from agent_construction_lab.fixed_model.study import StudyConfig


def _require(value, message):
    if not value:
        raise ValueError(message)


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _lines(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rank(rows, tolerance=1e-10):
    """Diagnostic numerical rank; not used to fit or modify any constructor."""
    matrix = [list(map(float, row)) for row in rows]
    rank = 0
    for column in range(len(matrix[0])):
        pivot = max(range(rank, len(matrix)), key=lambda i: abs(matrix[i][column]), default=None)
        if pivot is None or abs(matrix[pivot][column]) <= tolerance:
            continue
        matrix[rank], matrix[pivot] = matrix[pivot], matrix[rank]
        divisor = matrix[rank][column]
        matrix[rank] = [v / divisor for v in matrix[rank]]
        for index in range(rank + 1, len(matrix)):
            factor = matrix[index][column]
            matrix[index] = [a - factor * b for a, b in zip(matrix[index], matrix[rank])]
        rank += 1
        if rank == len(matrix):
            break
    return rank


def numeric_diagnosis(library, domain, settings, condition, saved, frozen_harness):
    rebuilt = construct_domain(condition, domain_id=domain, public_profile=settings["profile"],
                               features=settings["features"], library=library)
    predictions = rebuilt["metadata"]["predictions"]
    _require(len(predictions) == len(saved["predictions"]), "Stored prediction count mismatch")
    for expected, recorded in zip(predictions, saved["predictions"]):
        _require(expected["modules"] == recorded["modules"], "Stored candidate order mismatch")
        for key in ("objective", "predicted_score_effect", "predicted_token_effect"):
            x, y = expected[key], recorded[key]
            _require(x is None and y is None or x is not None and y is not None
                     and math.isclose(x, y, rel_tol=1e-10, abs_tol=1e-9), "Stored numerical prediction mismatch")
    _require(rebuilt["harness"] == frozen_harness, "Recomputed constructor selection differs from frozen harness")
    _require(rebuilt["metadata"]["numeric_learner"]["score"] == saved["numeric_learner"]["score"],
             "Stored score learner differs from source refit")
    best = max(row["objective"] for row in predictions)
    tied = [i for i, row in enumerate(predictions) if row["objective"] == best]
    selected = next(i for i, row in enumerate(predictions) if row["modules"] == frozen_harness["modules"])
    coefficients = rebuilt["metadata"]["numeric_learner"]["score"]["coefficients"]
    return dict(condition=condition, domain_id=domain, exact_source_refit_matches=True,
        score_target="paired score minus baseline" if condition == "F" else "absolute task score",
        score_coefficients=len(coefficients), nonzero_score_coefficients=sum(value != 0 for value in coefficients),
        candidate_predictions=[dict(candidate_id=f"c{i:02d}", **row) for i, row in enumerate(predictions)],
        maximum_objective=best, tied_maximum_candidates=[f"c{i:02d}" for i in tied],
        selected_candidate_id=f"c{selected:02d}", tie_break="first maximum in deterministic candidate order",
        cost_weight=library.config.cost_weight, token_prediction_influences_objective=library.config.cost_weight != 0)


def support_callbacks(episodes, manifest, config, search):
    """Recover complete callback blocks without publishing any task text/IDs."""
    support_seeds = config.support_seeds if config.support_seeds is not None else config.execution_seeds
    task_order = [(task["task_id"], seed) for task in manifest["tasks"]
                  if task["split"] == "support" and task["domain_id"] == search["domain"] for seed in support_seeds]
    selected = [row for row in episodes if row["stage"] == "support" and row["condition"] == search["condition"]
                and row["domain"] == search["domain"] and row["construction_seed"] == search["construction_seed"]]
    size = len(task_order)
    _require(size and len(selected) % size == 0, "Support callback has an incomplete task/seed block")
    callbacks = []
    for offset in range(0, len(selected), size):
        block = selected[offset:offset + size]
        _require([(row["task_id"], row["execution_seed"]) for row in block] == task_order,
                 "Support callback task/seed order differs from the frozen manifest")
        hashes = {row["harness_sha256"] for row in block}
        _require(len(hashes) == 1 and all(digest(row["harness"]) == row["harness_sha256"] for row in block),
                 "Support callback contains inconsistent harnesses")
        _require(all(row["valid_task_outcome"] for row in block), "Invalid support outcome cannot explain a selected harness")
        tokens = [row["usage"]["total_tokens"] for row in block]
        _require(all(type(value) is int and value >= 0 for value in tokens), "Support token usage is incomplete")
        values = [row["score"] - config.cost_weight * token for row, token in zip(block, tokens)]
        callbacks.append(dict(callback_index=len(callbacks) + 1, harness_sha256=next(iter(hashes)),
            episode_count=size, task_scores=[float(row["score"]) for row in block],
            objective=sum(values) / size, successful_episodes=sum(row["success"] for row in block),
            failure_types=dict(sorted(Counter(row["failure_type"] for row in block if row.get("failure_type")).items())),
            total_tokens=sum(tokens)))
    _require(len(callbacks) == search["audit"]["actual_evaluations"], "Support episode blocks disagree with GEPA audit")
    return callbacks


def diagnose_data(manifest, config, library_value, episodes, constructions, searches, harnesses, ledger):
    """Analyze approved fields only; callers may include unrelated private keys."""
    manifest = validate_manifest(manifest)
    library = ConstructorLibrary.from_dict(library_value)
    source = [row for row in episodes if row["stage"] == "source"]
    records = library.records
    _require(source and len(source) == len(records), "Source record/episode count mismatch")
    _require(all(row["valid_task_outcome"] for row in source), "Source contains invalid outcomes")
    matrix = [_basis(row["features"], row["modules"], library.feature_names, None)[1] for row in records]
    generic = make_harness()
    groups = defaultdict(list)
    builds = []
    numerics = []
    selectors = []
    ledger_ids = {row["attempt_id"]: row for row in ledger}
    for row in constructions:
        harness = harnesses[row["path"]]
        _require(digest(harness) == row["harness_sha256"], "Frozen construction hash mismatch")
        groups[row["harness_sha256"]].append(row["condition"])
        builds.append(dict(condition=row["condition"], harness_sha256=row["harness_sha256"], modules=harness["modules"],
            generic_system_prompt=harness["system_prompt"] == generic["system_prompt"],
            system_prompt_sha256=hashlib.sha256(harness["system_prompt"].encode("utf-8")).hexdigest(),
            constructor_attempts=len(row["attempt_ids"])))
        if row["condition"] in {"F", "V"}:
            numerics.append(numeric_diagnosis(library, row["domain"], manifest["domains"][row["domain"]],
                                            row["condition"], row["metadata"], harness))
        if row["condition"] == "S":
            candidates = row["metadata"]["candidate_library"]
            matching = [candidate["candidate_id"] for candidate in candidates if make_harness(candidate["modules"]) == harness]
            _require(len(matching) == 1, "S harness does not uniquely identify its selected candidate")
            attempts = [ledger_ids[a] for a in row["attempt_ids"]]
            _require(len(attempts) == 1 and attempts[0]["phase"] == "construction:S" and attempts[0]["success"] is True,
                     "S was not backed by exactly one successful construction call")
            selectors.append(dict(condition="S", selected_candidate_id=matching[0], model_calls=1,
                selection_evidence="unique inverse mapping from frozen harness under the validated selector contract",
                raw_response_persisted=False, model_rationale_observed=False))
    optimizations = []
    for search in searches:
        callbacks = support_callbacks(episodes, manifest, config, search)
        audit = search["audit"]
        single_equal_round = (len(callbacks) == 3 and audit["reflection_calls"] == 1
            and audit["invalid_candidate_evaluations"] == 0
            and callbacks[0]["harness_sha256"] == callbacks[1]["harness_sha256"]
            and callbacks[1]["objective"] == callbacks[2]["objective"])
        selected_hash = digest(search["selected_harness"])
        if single_equal_round:
            _require(selected_hash == callbacks[0]["harness_sha256"]
                and search["selected_score"] == callbacks[0]["objective"], "Equal proposal did not preserve the initial validation winner")
            for callback, role in zip(callbacks, ("initial_full_validation", "parent_remeasurement", "reflected_proposal")):
                callback["inferred_role"] = role
        optimizations.append(dict(condition=search["condition"], callbacks=callbacks,
            audit={key: audit[key] for key in ("actual_evaluations", "candidate_evaluation_requests", "invalid_candidate_evaluations",
                "reflection_calls", "cache_evaluation", "gepa_metric_calls", "unused_evaluation_budget")},
            selected_harness_sha256=selected_hash, selected_score=search["selected_score"],
            unique_evaluated_harnesses=len({row["harness_sha256"] for row in callbacks}),
            single_equal_round=single_equal_round,
            proposal_accepted=False if single_equal_round else None,
            selection_explanation="Equal parent/proposal score fails default strict improvement; initial full-validation winner is retained."
                if single_equal_round else "Consult the recorded callback sequence; no single-round rejection inference was made.",
            role_evidence="Callback grouping is checked against recorded task/seed order; roles and rejection are reconstructed from pinned GEPA control flow, not saved rejection events."))
    return dict(schema_version=1, kind="source_support_harness_diagnosis", no_new_model_calls=True, query_outcomes_used=False,
        source=dict(episodes=len(source), records=len(records), tasks=len({r["task_id"] for r in records}),
            generators=len({t["generator_id"] for t in manifest["tasks"] if t["split"] == "source"}),
            domains=len({r["domain_id"] for r in records}), unique_feature_vectors=len({canonical_json(r["features"]) for r in records}),
            successful_episodes=sum(r["success"] for r in source),
            score_distribution=dict(sorted(Counter(str(float(r["score"])) for r in source).items())),
            paired_effect_distribution=dict(sorted(Counter(str(float(r["score"] - r["baseline_score"])) for r in records).items())),
            failure_types=dict(sorted(Counter(r["failure_type"] for r in source if r.get("failure_type")).items())),
            basis_columns=len(matrix[0]), basis_numerical_rank=_rank(matrix), ridge_alpha=library.config.ridge_alpha,
            source_data_sha256=library.source_data_sha256),
        harnesses=builds, equal_harness_groups=[dict(harness_sha256=key, conditions=sorted(value)) for key, value in sorted(groups.items())],
        numeric_constructors=numerics, selector=selectors, feedback_optimization=optimizations,
        limitations=["One source generator and constant public features cannot identify cross-domain conditional effects.",
            "Zero fitted residual variance on constant zero labels is not evidence of generalization or low epistemic uncertainty.",
            "Native task completion and actor ending-protocol validity are different fields; protocol failures are retained.",
            "Identical harnesses are not evidence of equivalent algorithms or zero achievable feedback-optimization benefit.",
            "No task text, profiles, harness text, traces, provider bodies or query outcomes are copied into this report."])


def _gepa_evidence():
    import gepa
    from gepa.optimize_anything import EngineConfig
    root = Path(gepa.__file__).resolve().parent
    names = ("core/engine.py", "core/result.py", "strategies/acceptance.py", "optimize_anything.py",
             "proposer/reflective_mutation/reflective_mutation.py")
    _require(version("gepa") == "0.1.4", "Diagnosis requires the run's pinned GEPA version")
    return dict(version=version("gepa"), default_acceptance=EngineConfig().acceptance_criterion,
                source_sha256={name: _sha(root / name) for name in names})


def _historical_harness(directory, entry, provenance):
    """Verify a historical artifact against its recorded runtime, not new edits."""
    frozen = _json(directory / "manifest.json")
    harness = _json(directory / "harness.json")
    _require(digest(frozen) == entry["manifest_sha256"] and digest(harness) == frozen["harness_sha256"],
             "Historical frozen artifact hash mismatch")
    expected = {name: provenance["source_sha256"]["agent_construction_lab/fixed_model/" + name] for name in RUNTIME_FILES}
    _require(frozen["runtime_files"] == expected, "Historical harness runtime differs from original run provenance")
    return validate_harness(harness)


def diagnose_run(run, original_run):
    run, original = Path(run).resolve(), Path(original_run).resolve()
    status = _json(run / "run_status.json")
    _require(status.get("completed") is True, "Only a completed run can be diagnosed")
    names = ("task_manifest.json", "study_config.json", "constructor_library.json", "source_pairs.jsonl", "episodes.jsonl",
             "model_usage.jsonl", "constructions.jsonl", "support_searches.jsonl", "run_provenance.json", "resume_amendment.json", "run_status.json")
    before = {name: _sha(run / name) for name in names}
    amendment = _json(run / "resume_amendment.json")
    original_hashes = amendment["source_snapshot_sha256"]
    _require(digest(original_hashes) == amendment["source_anchor"], "Original snapshot anchor is inconsistent")
    _require(all(_sha(original / name) == expected for name, expected in original_hashes.items()), "Original evidence changed")
    provenance = _json(run / "run_provenance.json")
    numerical_sources = ("agent_construction_lab/fixed_model/constructors.py", "agent_construction_lab/learning.py",
        "agent_construction_lab/_io.py", "agent_construction_lab/fixed_model/spec.py",
        "agent_construction_lab/fixed_model/study.py", "agent_construction_lab/fixed_model/gepa_adapter.py")
    _require(all(_sha(ROOT / name) == provenance["source_sha256"][name] for name in numerical_sources),
             "Use the recorded runtime revision for exact numerical reconstruction")
    episodes = [row for row in _lines(run / "episodes.jsonl") if row["stage"] in {"source", "support"}]
    source = [row for row in episodes if row["stage"] == "source"]
    _require(source == _lines(original / "episodes.jsonl"), "Imported source episodes differ from original")
    _require(_lines(run / "source_pairs.jsonl") == _lines(original / "source_pairs.jsonl"), "Imported source pairs differ from original")
    library_value = _json(run / "constructor_library.json")
    _require(library_value == _json(original / "constructor_library.json")
        and library_value["records"] == _lines(run / "source_pairs.jsonl"), "Source library differs from the original paired evidence")
    builds = _lines(run / "constructions.jsonl")
    harnesses = {}
    for row in builds:
        directory = (run / row["path"]).resolve()
        _require(directory.is_relative_to(run), "Construction path escapes evidence directory")
        harnesses[row["path"]] = _historical_harness(directory, row, provenance)
        for name in ("manifest.json", "harness.json"):
            before[(directory / name).relative_to(run).as_posix()] = _sha(directory / name)
    report = diagnose_data(_json(run / "task_manifest.json"), StudyConfig.from_dict(_json(run / "study_config.json")),
        library_value, episodes, builds, _lines(run / "support_searches.jsonl"), harnesses,
        _lines(run / "model_usage.jsonl"))
    report["evidence"] = dict(run_id=status["run_id"], original_run_id=amendment["original_run_id"],
        code_revision=provenance["code_revision"], run_file_sha256=before, original_file_sha256=original_hashes,
        source_anchor=amendment["source_anchor"], source_reuse_identical=True, project_source_sha256=provenance["source_sha256"],
        numerical_reconstruction_source_matches=True, frozen_runtime_matches_run_provenance=True,
        gepa=_gepa_evidence())
    _require(all(_sha(run / name) == expected for name, expected in before.items()), "Evidence changed during diagnosis")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--original-run", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    output = Path(args.out).resolve()
    _require(not output.exists(), "Diagnostic output already exists; evidence is never overwritten")
    _require(not any(output.is_relative_to(Path(path).resolve()) for path in (args.run, args.original_run)),
             "Diagnostic output must be outside immutable evidence directories")
    report = diagnose_run(args.run, args.original_run)
    write_json(output, report)
    print(json.dumps({"written": str(output), "source_episodes": report["source"]["episodes"],
                      "unique_frozen_harnesses": len(report["equal_harness_groups"]), "new_model_calls": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
