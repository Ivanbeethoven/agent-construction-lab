"""Fixed-model CLI. Smoke is explicitly scripted and uses real localhost HTTP."""
import argparse
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
from urllib.parse import urlsplit
from .._io import write_json
from .client import ModelConfig
from .mock_server import local_mock_model, mock_manifest
from .study import StudyConfig, run_study
from .spec import load_frozen_harness, digest


def doctor(model_config, appworld_root=None):
    """Readiness only: never makes a billable model request."""
    value=json.loads(Path(model_config).read_text(encoding="utf-8"))
    problems=[]
    config=None
    try: config=ModelConfig.from_dict(value)
    except (ValueError,TypeError) as error: problems.append(str(error))
    if config:
        for field in ("model","model_snapshot","expected_response_model"):
            content=getattr(config,field)
            if not content or "REPLACE" in content.upper(): problems.append(f"Configure a concrete {field}")
        host=urlsplit(config.base_url).hostname or ""
        if "example" in host or "REPLACE" in config.base_url.upper(): problems.append("Configure a real endpoint")
        if config.api_key_env and not os.environ.get(config.api_key_env): problems.append(f"Set environment variable {config.api_key_env}")
    dependencies={}
    for name,expected in (("gepa","0.1.4"),("appworld","0.1.3.post1")):
        try: installed=version(name)
        except PackageNotFoundError: installed=None
        dependencies[name]=dict(installed=installed,expected=expected)
        if installed!=expected: problems.append(f"Install {name}=={expected}")
    if not appworld_root or not Path(appworld_root).is_dir(): problems.append("Configure the local AppWorld data root")
    return dict(ready_for_live_smoke=not problems,live_model_probed=False,problems=problems,
        dependency_versions=dependencies,credentials_disclosed=False)


def verify_run(root):
    root=Path(root).resolve()
    status=json.loads((root/"run_status.json").read_text(encoding="utf-8"))
    if not status.get("completed"): raise ValueError("Run is not complete")
    lock=json.loads((root/"query_lock.json").read_text(encoding="utf-8"))
    if lock.get("all_constructions_frozen_before_query") is not True: raise ValueError("Missing query barrier")
    for plan in lock["plans"]:
        path=(root/plan["path"]).resolve()
        if not path.is_relative_to(root): raise ValueError("Artifact path escapes run directory")
        harness=load_frozen_harness(path,expected_manifest_hash=plan["manifest_sha256"])
        if digest(harness)!=plan["harness_sha256"]: raise ValueError("Query harness digest differs")
    return dict(verified=True,frozen_harnesses=len(lock["plans"]),mock_model=status.get("mock_model"),
        scope="Frozen harness and runtime hashes; not a scientific-performance validation")


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    smoke=sub.add_parser("smoke");smoke.add_argument("--out",required=True)
    verify=sub.add_parser("verify-run");verify.add_argument("--run",required=True)
    health=sub.add_parser("doctor");health.add_argument("--model-config",required=True);health.add_argument("--appworld-root");health.add_argument("--out")
    run=sub.add_parser("run");run.add_argument("--manifest",required=True);run.add_argument("--model-config",required=True);run.add_argument("--study-config");run.add_argument("--appworld-root");run.add_argument("--out",required=True)
    prepare=sub.add_parser("prepare-appworld");prepare.add_argument("--appworld-root",required=True);prepare.add_argument("--out",required=True)
    args=parser.parse_args(argv)
    if args.command=="smoke":
        with local_mock_model() as model:
            result=run_study(mock_manifest(),model,args.out,config=StudyConfig(max_steps=4,bootstrap_samples=100),mock=True)
        print(json.dumps(dict(completed=result["completed"],mock_model=True,conditions=list(result["methods"]),
            actual_episode_counts=result["actual_episode_counts"],total_actual_usage=result["total_actual_usage"]),ensure_ascii=False,indent=2))
    elif args.command=="verify-run": print(json.dumps(verify_run(args.run),indent=2))
    elif args.command=="doctor":
        report=doctor(args.model_config,args.appworld_root)
        if args.out:write_json(args.out,report)
        print(json.dumps(report,indent=2));return 0 if report["ready_for_live_smoke"] else 1
    elif args.command=="prepare-appworld":
        from .environments import prepare_appworld_manifest
        result=prepare_appworld_manifest(args.appworld_root);write_json(args.out,result)
        print(json.dumps({"manifest":args.out,"tasks":len(result["tasks"])}))
    else:
        readiness=doctor(args.model_config,args.appworld_root)
        if not readiness["ready_for_live_smoke"]:
            print(json.dumps(readiness,indent=2));return 1
        manifest=json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        config=StudyConfig.from_dict(json.loads(Path(args.study_config).read_text())) if args.study_config else StudyConfig()
        result=run_study(manifest,ModelConfig.from_dict(json.loads(Path(args.model_config).read_text())),args.out,
            config=config,appworld_root=args.appworld_root)
        print(json.dumps({"completed":result["completed"],"out":args.out}))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
