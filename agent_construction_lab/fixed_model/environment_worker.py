"""Private worker entry point. Only a fixed JSON dispatcher crosses the pipe."""
from __future__ import annotations

import json
import os
import sys
import traceback


def main():
    # Keep a separate protocol descriptor before redirecting fd 1, so both
    # Python print() and C/library os.write(1, ...) noise go to host diagnostics.
    sys.stdout.flush()
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1, newline="\n")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    # AppWorld 0.1.3.post1 imports dotenv.load_dotenv during startup. Older
    # python-dotenv versions do not honor PYTHON_DOTENV_DISABLED, so disable
    # that implicit disk read explicitly in this disposable worker as well.
    try:
        import dotenv
    except ModuleNotFoundError:
        pass  # A missing optional AppWorld dependency is diagnosed on init.
    else:
        dotenv.load_dotenv = lambda *args, **kwargs: False
        dotenv.main.load_dotenv = dotenv.load_dotenv
    environment = None
    for line in sys.stdin:
        request_id = None
        try:
            request = json.loads(line)
            request_id = request["id"]
            operation = request["operation"]
            payload = request.get("payload", {})
            if operation not in {"init", "execute", "evaluate", "close"} or not isinstance(payload, dict):
                raise ValueError("Unsupported protocol operation")
            if operation == "init":
                if environment is not None or set(payload) != {"task", "appworld_root", "experiment_name", "seed", "max_steps", "timeout_seconds"}:
                    raise ValueError("Invalid initialization")
                if not isinstance(payload["task"], dict) or set(payload["task"]) != {"task_id"}:
                    raise ValueError("Initialization accepts only a task ID")
                from . import environments
                environment = environments.AppWorldEnvironment(**payload)
                result = {"instruction": environment.instruction, "public_context": environment.public_context}
            else:
                if environment is None:
                    raise ValueError("Environment is not initialized")
                if operation == "execute":
                    if set(payload) != {"code"} or not isinstance(payload["code"], str):
                        raise ValueError("Invalid execution request")
                    result = environment.execute(payload["code"])
                else:
                    if payload:
                        raise ValueError("Operation accepts no payload")
                    result = environment.evaluate() if operation == "evaluate" else environment.close()
            protocol.write(json.dumps({"id": request_id, "ok": True, "result": result}, ensure_ascii=False, allow_nan=False) + "\n")
            protocol.flush()
            if operation == "close":
                return 0
        except BaseException as error:
            traceback.print_exc(file=sys.stderr)
            sys.stderr.flush()
            protocol.write(json.dumps({"id": request_id, "ok": False,
                                       "error": {"error_type": type(error).__name__}}, ensure_ascii=False) + "\n")
            protocol.flush()
            return 1
    if environment is not None:
        environment.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
