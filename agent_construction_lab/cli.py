"""Simulator CLI; fixed-model experiments have a separate entry point."""
import argparse
import json
from pathlib import Path
from .contracts import TaskSpec
from .artifacts import compile_artifact, verify_artifact, run_artifact
from .experiments import run_pilot
from ._io import write_json


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="command",required=True)
    build=sub.add_parser("build"); build.add_argument("--task",required=True); build.add_argument("--mechanisms",nargs="*",default=[]); build.add_argument("--out",required=True)
    verify=sub.add_parser("verify"); verify.add_argument("--artifact",required=True)
    run=sub.add_parser("run"); run.add_argument("--artifact",required=True); run.add_argument("--seed",type=int,default=0); run.add_argument("--out")
    pilot=sub.add_parser("pilot"); pilot.add_argument("--out",required=True); pilot.add_argument("--config")
    args=parser.parse_args(argv)
    if args.command=="build":
        result=vars(compile_artifact(TaskSpec.from_dict(json.loads(Path(args.task).read_text(encoding="utf-8"))),args.mechanisms,args.out))
    elif args.command=="verify": result=verify_artifact(args.artifact)
    elif args.command=="run":
        result=run_artifact(args.artifact,seed=args.seed).to_dict()
        if args.out: write_json(args.out,result)
    else:
        result=run_pilot(args.out,json.loads(Path(args.config).read_text()) if args.config else None)
    print(json.dumps(result,ensure_ascii=False))
    return 0
