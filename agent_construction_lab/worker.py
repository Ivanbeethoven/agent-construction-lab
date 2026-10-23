"""One trusted simulator request per subprocess."""
import json
import sys
from .contracts import TaskSpec
from .runtime import run_episode


def main():
    request = json.load(sys.stdin)
    if request.get("schema_version") != 1:
        raise ValueError("Unsupported protocol")
    result = run_episode(TaskSpec.from_dict(request["task"]), tuple(request["mechanisms"]), request["seed"])
    print(json.dumps(dict(schema_version=1, result=result.to_dict(), usage={"model_calls": 0})))


if __name__ == "__main__":
    main()
