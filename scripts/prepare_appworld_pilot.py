"""Prepare a tiny, generator-disjoint AppWorld engineering pilot without an LLM.

Only train/dev ID lists are opened. Query specs are hashed as opaque bytes;
their instructions are never decoded or used for selection/profile generation.
Raw source/support instructions are written only to the explicitly local output.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_construction_lab.fixed_model.constructors import candidate_module_sets
from agent_construction_lab.fixed_model.spec import digest, validate_manifest
from agent_construction_lab.fixed_model.study import StudyConfig


SELECTION_VERSION = "appworld-engineering-pilot-v1"
CALIBRATION_GENERATORS = ("82e2fac",)
PUBLIC_DESCRIPTION = (
    "Personal digital tasks in a stateful world of documented application APIs. "
    "Discover the relevant public APIs, carry out the requested operations, "
    "and check the resulting state before completing the task."
)
# Benchmark-level priors, fixed before task selection. They do not use query
# instructions, solution metadata, difficulty annotations, or task outcomes.
PUBLIC_FEATURES = {"documented_api_environment": 1.0, "stateful_application_world": 1.0}
TASK_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*_[A-Za-z0-9-]+\Z")


def file_sha256(path):
    """Hash bytes without interpreting the task content."""
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def official_groups(root):
    """Read exactly the two development ID lists, never official test lists."""
    result = {}
    seen = set()
    for split in ("train", "dev"):
        path = Path(root) / "data" / "datasets" / f"{split}.txt"
        grouped = defaultdict(list)
        for raw in path.read_text(encoding="utf-8").splitlines():
            task_id = raw.strip()
            if not task_id:
                continue
            if not TASK_ID_PATTERN.fullmatch(task_id) or task_id in seen:
                raise ValueError("Official train/dev lists contain an unsafe or duplicate task ID")
            seen.add(task_id)
            grouped[task_id.rsplit("_", 1)[0]].append(task_id)
        if not grouped:
            raise ValueError(f"No tasks in official {split} list")
        result[split] = {key: sorted(value) for key, value in grouped.items()}
    if set(result["train"]) & set(result["dev"]):
        raise ValueError("Official train/dev lists share a generator")
    return result


def ranked_generators(groups, seed, split):
    return sorted(groups, key=lambda group: hashlib.sha256(
        f"{SELECTION_VERSION}:{seed}:{split}:{group}".encode("utf-8")
    ).hexdigest())


def budget_report(manifest, config):
    """Count planned physical episodes; dev is reserved, never run by run_study."""
    if config.ablations:
        raise ValueError("The engineering pilot excludes extra ablation conditions")
    counts = Counter(task["split"] for task in manifest["tasks"])
    source = counts["source"] * len(candidate_module_sets(config.max_modules)) * len(config.source_seeds)
    support_seeds = config.execution_seeds if config.support_seeds is None else config.support_seeds
    r_methods = sum(method in {"G+R", "F+R"} for method in config.methods)
    support = counts["support"] * len(support_seeds) * sum(config.rsi_budgets) * r_methods * len(config.construction_seeds)
    conditions = len(config.methods) - r_methods + r_methods * len(config.rsi_budgets)
    query = counts["query"] * len(config.execution_seeds) * len(config.construction_seeds) * conditions
    return {
        "tasks_by_role": dict(counts),
        "planned_episode_upper_bounds": {"source": source, "dev": 0, "support": support, "query": query, "total": source + support + query},
        "conditions": conditions,
        "r_budget_unit": "evaluator_callback; each expands complete domain support x support_seeds",
        "support_seeds": list(support_seeds),
        "max_steps_per_episode": config.max_steps,
        "model_requests_are_not_episode_counts": True,
        "infrastructure_retries_included": False,
        "real_model_called": False,
    }


def build_pilot(root, config, *, seed=20261024, excluded_generators=CALIBRATION_GENERATORS):
    """Select whole generators by ID hash, then read only source/support specs."""
    if type(seed) is not int or seed < 0:
        raise ValueError("Selection seed must be a nonnegative integer")
    root = Path(root)
    groups = official_groups(root)
    excluded = set(CALIBRATION_GENERATORS) | set(excluded_generators)
    if any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", value) for value in excluded):
        raise ValueError("Excluded generator IDs must be safe nonempty identifiers")
    train = [value for value in ranked_generators(groups["train"], seed, "train") if value not in excluded]
    dev = [value for value in ranked_generators(groups["dev"], seed, "dev") if value not in excluded]
    if not train or len(dev) < 2:
        raise ValueError("Need one train generator and two dev generators")
    assignments = [
        ("source", "source.personal_apps", "train", train[0]),
        ("support", "target.personal_apps", "dev", dev[0]),
        ("query", "target.personal_apps", "dev", dev[1]),
    ]
    # IDs are fixed before reading any instruction. Missing or incompatible data
    # fails preparation; it never silently replaces a task by a better-looking one.
    for _, _, split, generator in assignments:
        if len(groups[split][generator]) != 3:
            raise ValueError("This micro-pilot requires exactly three variants per selected generator")

    domains, tasks, local_instructions = {}, [], defaultdict(list)
    for role, domain, split, generator in assignments:
        domains.setdefault(domain, {
            "profile": {"description": PUBLIC_DESCRIPTION, "profile_source": "Handwritten benchmark-level prior; no held-out task content"},
            "features": dict(PUBLIC_FEATURES),
        })
        for task_id in groups[split][generator]:
            path = root / "data" / "tasks" / task_id / "specs.json"
            tasks.append({"task_id": task_id, "generator_id": generator,
                          "domain_id": domain, "split": role, "benchmark_split": split,
                          "public_spec_sha256": file_sha256(path)})
            if role in {"source", "support"}:
                spec = json.loads(path.read_text(encoding="utf-8"))
                instruction = spec.get("instruction")
                if not isinstance(instruction, str) or not instruction.strip():
                    raise ValueError("Selected source/support spec lacks its public instruction")
                local_instructions[domain].append({"task_id": task_id, "instruction": instruction})

    public = validate_manifest({
        "schema_version": 1,
        "benchmark": "appworld",
        "official_protocol": False,
        "group_disjoint_support_query": True,
        "name": "Tiny cross-generator AppWorld engineering pilot (not a research-performance claim)",
        "selection": {"version": SELECTION_VERSION, "seed": seed,
                      "method": "Whole train/dev generators ordered by SHA-256 of version/seed/split/ID",
                      "excluded_calibration_generators": sorted(excluded),
                      "semantic_domain_claim": "Shared broad benchmark domain; role IDs do not establish cross-semantic-domain transfer",
                      "instruction_content_used_for_selection": False,
                      "held_out_instructions_read": False,
                      "official_test_lists_read": False},
        "domains": domains,
        "tasks": tasks,
    })
    local = deepcopy(public)
    local["public_manifest_sha256"] = digest(public)
    for domain, values in local_instructions.items():
        role = "support" if domain.startswith("target.") else "source"
        local["domains"][domain]["profile"] = {
            "description": PUBLIC_DESCRIPTION,
            "profile_source": f"Handwritten benchmark prior plus selected public {role} instructions only",
            "public_examples": values,
        }
    local = validate_manifest(local)
    report = budget_report(local, config)
    report.update({
        "selection_version": SELECTION_VERSION,
        "selection_seed": seed,
        "public_manifest_sha256": digest(public),
        "local_manifest_sha256": digest(local),
        "study_config_sha256": digest(config.__dict__),
        "official_split_sha256": {name: file_sha256(root / "data" / "datasets" / f"{name}.txt") for name in ("train", "dev")},
        "official_split_task_counts": {name: sum(map(len, grouped.values())) for name, grouped in groups.items()},
        "selected_generators": {role: generator for role, _, _, generator in assignments},
        "source_generator_count": 1,
        "target_generator_count": 2,
        "dev_tasks_selected": False,
        "dev_tuning_is_executed": False,
        "scientific_scope": "Engineering pilot only: one source generator cannot identify domain-conditional transfer; three target tasks are not a powered comparison",
    })
    return public, local, report


def write_outputs(public, local, report, public_out, local_out, report_out):
    local_out, report_out, public_out = Path(local_out), Path(report_out), Path(public_out)
    # Deliberately require the existing Git-ignore namespace, rather than merely
    # relying on a caller to remember to ignore a newly chosen private filename.
    for path in (local_out, report_out):
        ignored_directory = any(a == "runs" and b.startswith("local") for a, b in zip(path.parts, path.parts[1:]))
        if not (path.name.endswith(".local.json") and ignored_directory):
            raise ValueError("Local outputs must be *.local.json inside a local* directory (for example runs/local-live/)")
    outputs = [(public_out, public), (local_out, local), (report_out, report)]
    if len({path.resolve() for path, _ in outputs}) != len(outputs):
        raise ValueError("Output paths must be distinct")
    if any(path.exists() for path, _ in outputs):
        raise FileExistsError("Refusing to overwrite an existing pilot selection")
    for path, value in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appworld-root", required=True)
    parser.add_argument("--study-config", default="configs/appworld_small_pilot.json")
    parser.add_argument("--seed", type=int, default=20261024)
    parser.add_argument("--exclude-generator", action="append", default=[], help="Additional calibration generators to exclude; 82e2fac is always excluded")
    parser.add_argument("--public-out", default="manifests/appworld_small_pilot.json")
    parser.add_argument("--local-out", default="runs/local-live/appworld_small_pilot.manifest.local.json")
    parser.add_argument("--report-out", default="runs/local-live/appworld_small_pilot.preparation.local.json")
    parser.add_argument("--dry-run", action="store_true", help="Validate real IDs/profiles and print hashes/budget; write no files")
    args = parser.parse_args(argv)
    config = StudyConfig.from_dict(json.loads(Path(args.study_config).read_text(encoding="utf-8")))
    public, local, report = build_pilot(args.appworld_root, config, seed=args.seed, excluded_generators=args.exclude_generator)
    if not args.dry_run:
        write_outputs(public, local, report, args.public_out, args.local_out, args.report_out)
    # This report deliberately excludes task instructions and local profiles.
    print(json.dumps({**report, "dry_run": args.dry_run}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
