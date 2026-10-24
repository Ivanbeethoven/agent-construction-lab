"""Prepare the independent second AppWorld pilot without reading query text.

Selection uses only official train/dev IDs. Calibration and query specs are
hashed as opaque bytes; only selected source/support instructions are decoded.
No provider request, environment episode, or official test read is performed.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_construction_lab.fixed_model.spec import digest, validate_manifest
from agent_construction_lab.fixed_model.study import StudyConfig
from scripts.prepare_appworld_pilot import (
    PUBLIC_DESCRIPTION, PUBLIC_FEATURES, budget_report, file_sha256,
    official_groups, write_outputs,
)


SELECTION_VERSION = "appworld-engineering-round2-v1"
SELECTION_SEED = 20261025
PRIOR_CONTACT_GENERATORS = ("82e2fac", "afc0fce", "6c2c621", "4ec8de5")
METHODS = ("G", "D", "S", "V", "F", "G+R", "F+R")


def default_study_config():
    return StudyConfig(source_seeds=(101,), support_seeds=(101,), execution_seeds=(101,),
        construction_seeds=(17,), rsi_budgets=(4,), methods=METHODS, ablations=(),
        max_steps=24, max_context_chars=60000, max_modules=2, bootstrap_samples=100,
        ridge_alpha=2.0, cost_weight=0.0, require_complete_token_usage=True,
        source_summary_max_chars=24000, support_feedback_max_chars=24000,
        appworld_timeout_seconds=60, appworld_initialization_timeout_seconds=120)


def _validate_design(config):
    expected = default_study_config()
    # These axes determine the registered 66 + 24 + 21 episode budget. A later
    # design needs an explicit new preparation, not silent resampling here.
    for name in ("source_seeds", "support_seeds", "execution_seeds", "construction_seeds",
                 "rsi_budgets", "methods", "ablations"):
        actual = getattr(config, name)
        if actual is None or tuple(actual) != tuple(getattr(expected, name)):
            raise ValueError("Round-two registered axis changed: " + name)
    for name in ("max_steps", "max_modules", "require_complete_token_usage"):
        if getattr(config, name) != getattr(expected, name):
            raise ValueError("Round-two registered execution setting changed: " + name)


def ranked_generators(groups, split):
    def key(generator):
        salt = f"{SELECTION_VERSION}:{SELECTION_SEED}:{split}:{generator}"
        return hashlib.sha256(salt.encode("utf-8")).hexdigest(), generator
    return sorted((g for g in groups if g not in PRIOR_CONTACT_GENERATORS), key=key)


def build_round2(root, config=None):
    config = default_study_config() if config is None else config
    _validate_design(config)
    root = Path(root)
    groups = official_groups(root)
    train, dev = ranked_generators(groups["train"], "train"), ranked_generators(groups["dev"], "dev")
    if len(train) < 3 or len(dev) < 2:
        raise ValueError("Need three untouched train generators and two untouched dev generators")
    calibration_generator = train[0]
    assignments = [
        ("source", "source.personal_apps", "train", train[1]),
        ("source", "source.personal_apps", "train", train[2]),
        ("support", "target.personal_apps", "dev", dev[0]),
        ("query", "target.personal_apps", "dev", dev[1]),
    ]
    selected_groups = [("train", calibration_generator)] + [(split, generator) for _, _, split, generator in assignments]
    # Check after deterministic selection. A missing variant fails; it must not
    # cause the selector to skip a selected generator and quietly choose another.
    if any(len(groups[split][generator]) != 3 for split, generator in selected_groups):
        raise ValueError("Every selected generator must contain exactly three variants; no replacements are allowed")

    calibration_ids = groups["train"][calibration_generator]
    calibration = {
        "generator_id": calibration_generator, "benchmark_split": "train",
        "task_ids": calibration_ids, "initial_protocol_task_id": calibration_ids[0],
        "whole_generator_excluded_from_main_study": True,
        "planned_initial_episodes": 1, "max_actor_requests_initial_episode": config.max_steps,
        "remaining_variants_reserved_only": True,
        "public_spec_sha256": {task_id: file_sha256(root / "data" / "tasks" / task_id / "specs.json")
                               for task_id in calibration_ids},
    }
    domains, tasks, local_examples = {}, [], defaultdict(list)
    for role, domain, split, generator in assignments:
        domains.setdefault(domain, {
            "profile": {"description": PUBLIC_DESCRIPTION,
                        "profile_source": "Handwritten benchmark-level prior; no held-out task content"},
            "features": dict(PUBLIC_FEATURES),
        })
        for task_id in groups[split][generator]:
            path = root / "data" / "tasks" / task_id / "specs.json"
            tasks.append({"task_id": task_id, "generator_id": generator, "domain_id": domain,
                          "split": role, "benchmark_split": split, "public_spec_sha256": file_sha256(path)})
            if role in {"source", "support"}:
                instruction = json.loads(path.read_text(encoding="utf-8")).get("instruction")
                if not isinstance(instruction, str) or not instruction.strip():
                    raise ValueError("Selected source/support spec lacks its public instruction")
                local_examples[domain].append({"task_id": task_id, "instruction": instruction})

    split_hashes = {split: file_sha256(root / "data" / "datasets" / f"{split}.txt") for split in ("train", "dev")}
    public = validate_manifest({
        "schema_version": 1, "benchmark": "appworld", "official_protocol": False,
        "group_disjoint_support_query": True,
        "name": "Independent second AppWorld engineering pilot; descriptive results only",
        "selection": {
            "version": SELECTION_VERSION, "seed": SELECTION_SEED,
            "method": "Whole generators ordered by SHA-256(version:seed:official_split:generator_id), then generator ID",
            "rank_assignment": "train rank 1 calibration; train ranks 2/3 source; dev rank 1 support; dev rank 2 query",
            "excluded_prior_contact_generators": sorted(PRIOR_CONTACT_GENERATORS),
            "excluded_calibration_generators": sorted({"82e2fac", calibration_generator}),
            "excluded_main_study_generators": sorted(set(PRIOR_CONTACT_GENERATORS) | {calibration_generator}),
            "official_split_sha256": split_hashes,
            "semantic_domain_claim": "One broad personal-apps domain; role IDs do not establish semantic-domain transfer",
            "instruction_content_used_for_selection": False, "held_out_instructions_read": False,
            "calibration_instructions_read_during_preparation": False, "official_test_lists_read": False,
            "replacement_based_on_outcomes_allowed": False,
        },
        "calibration": calibration, "domains": domains, "tasks": tasks,
    })
    local = deepcopy(public)
    local["public_manifest_sha256"] = digest(public)
    for domain, examples in local_examples.items():
        role = "support" if domain.startswith("target.") else "source"
        local["domains"][domain]["profile"] = {
            "description": PUBLIC_DESCRIPTION,
            "profile_source": f"Handwritten benchmark prior plus selected public {role} instructions only",
            "public_examples": examples,
        }
    local = validate_manifest(local)
    report = budget_report(local, config)
    actor_requests = report["planned_episode_upper_bounds"]["total"] * config.max_steps
    report.update({
        "selection_version": SELECTION_VERSION, "selection_seed": SELECTION_SEED,
        "public_manifest_sha256": digest(public), "local_manifest_sha256": digest(local),
        "study_config_sha256": digest(asdict(config)), "official_split_sha256": split_hashes,
        "official_split_task_counts": {split: sum(map(len, values.values())) for split, values in groups.items()},
        "selected_generators": {role: [generator for r, _, _, generator in assignments if r == role]
                                for role in ("source", "support", "query")},
        "calibration": calibration, "source_generator_count": 2, "target_generator_count": 2,
        "dev_tasks_selected": False, "dev_tuning_is_executed": False,
        "planned_requests": {
            "main_actor_upper_bound": actor_requests,
            "nominal_builder_and_reflection_calls": 4,
            "nominal_main_requests": actor_requests + 4,
            "one_calibration_actor_upper_bound": config.max_steps,
            "one_provider_preflight_request": 1,
            "nominal_total_including_calibration_and_preflight": actor_requests + 4 + config.max_steps + 1,
            "reflection_note": "Four overhead calls assume D/S once and one GEPA reflection per R branch; global request cap remains authoritative.",
        },
        "declared_round_limits": {"max_model_calls_including_external": 2800,
            "max_total_tokens_including_external": 24000000, "max_output_tokens_per_request": 1024,
            "request_token_reserve": 1048576, "max_cost_usd": 10,
            "main_limits_must_subtract_actual_calibration_and_preflight_usage": True},
        "run_status": "selection_prepared_no_live_model_called",
        "runtime_and_model_must_be_frozen_separately": True,
        "scientific_scope": "Engineering pilot: two source generators with constant features do not identify conditional cross-domain transfer; one query generator has three correlated variants.",
    })
    return public, local, report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appworld-root", required=True)
    parser.add_argument("--study-config", help="Use the final parent-reviewed study config; otherwise use the registered round-two defaults")
    parser.add_argument("--public-out", default="manifests/appworld_round2.json")
    parser.add_argument("--local-out", default="runs/local-live/appworld_round2.manifest.local.json")
    parser.add_argument("--report-out", default="runs/local-live/appworld_round2.preparation.local.json")
    parser.add_argument("--dry-run", action="store_true", help="Validate IDs/profiles and print only metadata; write no files")
    args = parser.parse_args(argv)
    config = StudyConfig.from_dict(json.loads(Path(args.study_config).read_text(encoding="utf-8"))) if args.study_config else default_study_config()
    public, local, report = build_round2(args.appworld_root, config)
    if not args.dry_run:
        write_outputs(public, local, report, args.public_out, args.local_out, args.report_out)
    print(json.dumps({**report, "dry_run": args.dry_run}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
