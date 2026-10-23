"""Validated study partitions and externally anchored, portable harness artifacts."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path

from agent_construction_lab._io import canonical_json, digest

MODULES = ("plan", "verify", "tool_docs", "error_repair")
RUNTIME_FILES = ("executor.py", "client.py", "environments.py", "spec.py",
                 "appworld_process.py", "environment_worker.py", "code_policy.py")


def validate_harness(harness):
    if not isinstance(harness, dict) or set(harness) != {"schema_version", "system_prompt", "modules"}:
        raise ValueError("Harness must contain exactly schema_version, system_prompt and modules")
    if type(harness["schema_version"]) is not int or harness["schema_version"] != 1:
        raise ValueError("Unsupported harness schema_version")
    if not isinstance(harness["system_prompt"], str) or not harness["system_prompt"].strip():
        raise ValueError("Harness system_prompt must be nonempty text")
    modules = harness["modules"]
    if (not isinstance(modules, list) or len(modules) > 2 or
            any(not isinstance(item, str) or item not in MODULES for item in modules) or
            len(set(modules)) != len(modules)):
        raise ValueError("Harness modules must be unique, known, and at most two")
    return copy.deepcopy(harness)


def validate_manifest(manifest):
    if not isinstance(manifest, dict) or type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        raise ValueError("Unsupported task manifest schema")
    if manifest.get("benchmark") not in {"builtin", "appworld"}:
        raise ValueError("Unknown benchmark")
    domains, tasks = manifest.get("domains"), manifest.get("tasks")
    if not isinstance(domains, dict) or not domains or not isinstance(tasks, list) or not tasks:
        raise ValueError("Manifest requires domains and tasks")
    feature_schema = None
    private_words = {"query", "label", "success", "score", "outcome", "gold", "answer", "solution"}
    for name, domain in domains.items():
        if not isinstance(name, str) or not name or not isinstance(domain, dict):
            raise ValueError("Invalid public domain")
        if not isinstance(domain.get("profile"), dict) or not isinstance(domain.get("features"), dict):
            raise ValueError("Domains need a public profile and feature mapping")
        features = domain["features"]
        if not features or any(not isinstance(key, str) or private_words.intersection(key.lower().replace("-", "_").split("_")) for key in features):
            raise ValueError("Private labels cannot be construction features")
        if any(type(value) not in {int, float} or not math.isfinite(value) for value in features.values()):
            raise ValueError("Public domain features must be finite numbers")
        if feature_schema is None:
            feature_schema = set(features)
        elif set(features) != feature_schema:
            raise ValueError("All domains must share the same feature schema")
    strict_groups=manifest.get("group_disjoint_support_query",False)
    if type(strict_groups) is not bool:
        raise ValueError("group_disjoint_support_query must be boolean")
    ids, domain_roles, generator_roles = set(), {}, {}
    target_generator_splits={}
    for task in tasks:
        if not isinstance(task, dict):
            raise ValueError("Invalid task entry")
        task_id, domain, split = task.get("task_id"), task.get("domain_id"), task.get("split")
        if not isinstance(task_id, str) or not task_id or task_id in ids:
            raise ValueError("Task IDs must be nonempty and globally unique")
        ids.add(task_id)
        if domain not in domains or split not in {"source", "dev", "support", "query"}:
            raise ValueError("Unknown domain or task role")
        role = "target" if split in {"support", "query"} else split
        if domain_roles.setdefault(domain, role) != role:
            raise ValueError("Source, dev and target domains must be disjoint")
        generator = task.get("generator_id")
        if strict_groups and split in {"support","query"}:
            if not isinstance(generator,str) or not generator:
                raise ValueError("Strict transfer requires generator_id on every support/query task")
            if target_generator_splits.setdefault(generator,split)!=split:
                raise ValueError("Strict transfer requires disjoint support/query generators")
        if generator is not None:
            if not isinstance(generator, str) or not generator or generator_roles.setdefault(generator, role) != role:
                raise ValueError("Generators cannot cross study roles")
        if manifest["benchmark"] == "appworld":
            if not generator or generator != task_id.rsplit("_", 1)[0]:
                raise ValueError("AppWorld generator_id must match the task ID prefix")
            official = task.get("benchmark_split")
            if official not in {"train", "dev", "test_normal", "test_challenge"}:
                raise ValueError("Missing or unknown AppWorld official split")
            if split in {"source", "dev"} and official != "train":
                raise ValueError("Source and dev feedback must use official train tasks")
            if split == "support" and official not in {"train", "dev"}:
                raise ValueError("Official tests cannot supply support feedback")
    support = {task["domain_id"] for task in tasks if task["split"] == "support"}
    if any(task["domain_id"] not in support for task in tasks if task["split"] == "query"):
        raise ValueError("Each query domain requires disjoint support tasks")
    return copy.deepcopy(manifest)


def _runtime_hashes():
    root = Path(__file__).resolve().parent
    return {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in RUNTIME_FILES}


def freeze_harness(harness, directory, metadata=None):
    harness = validate_harness(harness)
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "harness_sha256": digest(harness),
                "metadata": copy.deepcopy(metadata or {}), "runtime_files": _runtime_hashes()}
    for name, value in (("harness.json", harness), ("manifest.json", manifest)):
        (directory / name).write_text(canonical_json(value) + "\n", encoding="utf-8", newline="\n")
    return {"path": str(directory), "manifest_sha256": digest(manifest), "harness_sha256": digest(harness)}


def load_frozen_harness(directory, *, expected_manifest_hash):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if digest(manifest) != expected_manifest_hash:
        raise ValueError("Frozen manifest changed")
    dependencies = manifest.get("runtime_files")
    if not isinstance(dependencies, dict) or set(dependencies) != set(RUNTIME_FILES):
        raise ValueError("Unexpected runtime dependency")
    for name, current in _runtime_hashes().items():
        if dependencies[name] != current:
            raise ValueError(f"Runtime dependency changed: {name}")
    harness = json.loads((directory / "harness.json").read_text(encoding="utf-8"))
    if digest(harness) != manifest.get("harness_sha256"):
        raise ValueError("Frozen harness changed")
    return validate_harness(harness)
