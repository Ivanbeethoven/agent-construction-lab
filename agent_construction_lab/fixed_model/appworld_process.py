"""Deadline-bound JSON-lines IPC with a fresh AppWorld worker per episode.

Pipe I/O runs in a helper thread so Windows write/read blocking is covered by
the same deadline. Every failure kills and waits for the worker. stderr uses a
temporary file, avoiding pipe-pressure deadlocks and keeping private diagnostic
text out of the public model feedback.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading


class AppWorldProcessError(RuntimeError):
    def __init__(self, operation, error_type, *, worker_pid=None, worker_returncode=None, stderr_tail="", stderr_truncated=False):
        super().__init__(f"AppWorld worker {operation} failed ({error_type}); host diagnostics retained separately")
        self.operation = operation
        self.error_type = error_type
        self.worker_pid = worker_pid
        self.worker_returncode = worker_returncode
        self.stderr_tail = stderr_tail
        self.stderr_truncated = stderr_truncated


class AppWorldProcessTimeout(AppWorldProcessError):
    pass


def _worker_environment(root, cache):
    # An allowlist rather than a list of known credential prefixes: unknown
    # service credentials and proxy URLs are excluded as well.
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE",
               "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "TEMP", "TMP", "TMPDIR",
               "LANG", "LC_ALL", "LC_CTYPE", "TZ"}
    env = {name: value for name, value in os.environ.items() if name.upper() in allowed}
    env.update(APPWORLD_ROOT=str(root), APPWORLD_CACHE=str(cache / "appworld"),
               IPYTHONDIR=str(cache / "ipython"), PYTHONIOENCODING="utf-8", PYTHONUTF8="1",
               PYTHONUNBUFFERED="1", PYTHONNOUSERSITE="1", PYTHON_DOTENV_DISABLED="1")
    return env


class AppWorldProcessEnvironment:
    def __init__(self, task, *, appworld_root, experiment_name, seed=101, max_steps=15,
                 timeout_seconds=30, initialization_timeout_seconds=120, _worker_argv=None):
        for value in (timeout_seconds, initialization_timeout_seconds):
            if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
                raise ValueError("Worker timeouts must be positive finite seconds")
        self.timeout_seconds = float(timeout_seconds)
        self.closed = False
        self.stderr_tail = ""
        self.stderr_truncated = False
        self.worker_pid = None
        self.worker_returncode = None
        self._request_id = 0
        self._process = None
        self._thread = None
        root = Path(appworld_root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        self._cache = tempfile.TemporaryDirectory(prefix="acl-worker-", dir=root)
        self._stderr = tempfile.TemporaryFile(mode="w+b")
        command = _worker_argv or [sys.executable, "-u", "-m", "agent_construction_lab.fixed_model.environment_worker"]
        try:
            self._process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self._stderr, env=_worker_environment(root, Path(self._cache.name)),
                cwd=self._cache.name, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), bufsize=0)
            self.worker_pid = self._process.pid
            result = self._request("init", {"task": {"task_id": task["task_id"]},
                "appworld_root": str(root), "experiment_name": experiment_name, "seed": seed,
                "max_steps": max_steps, "timeout_seconds": timeout_seconds}, initialization_timeout_seconds)
            if not isinstance(result, dict) or not isinstance(result.get("instruction"), str) or not isinstance(result.get("public_context"), str):
                self._raise("init", "InvalidResponse")
            self.instruction = result["instruction"]
            self.public_context = result["public_context"]
        except BaseException:
            self._stop()
            raise

    def _stop(self):
        if self.closed:
            return
        self.closed = True
        process = self._process
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
            self.worker_returncode = process.returncode
            if self._thread is not None:
                self._thread.join(timeout=1)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
        self._stderr.flush()
        size = self._stderr.seek(0, os.SEEK_END)
        self.stderr_truncated = size > 65536
        self._stderr.seek(max(0, size - 65536))
        self.stderr_tail = self._stderr.read().decode("utf-8", errors="replace")
        self._stderr.close()
        self._cache.cleanup()

    def _raise(self, operation, error_type, timeout=False):
        self._stop()
        cls = AppWorldProcessTimeout if timeout else AppWorldProcessError
        raise cls(operation, error_type, worker_pid=self.worker_pid,
                  worker_returncode=self.worker_returncode, stderr_tail=self.stderr_tail,
                  stderr_truncated=self.stderr_truncated)

    def _request(self, operation, payload, timeout=None):
        if self.closed:
            raise RuntimeError("AppWorld worker is closed")
        self._request_id += 1
        request_id = self._request_id
        request = (json.dumps({"id": request_id, "operation": operation, "payload": payload},
                              ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        replies = queue.Queue(maxsize=1)

        def exchange():
            try:
                # Unbuffered raw pipes may return partial writes.
                view = memoryview(request)
                while view:
                    count = self._process.stdin.write(view)
                    if not count:
                        raise BrokenPipeError("Worker input pipe closed")
                    view = view[count:]
                self._process.stdin.flush()
                line = self._process.stdout.readline(16 * 1024 * 1024 + 1)
                replies.put((True, line))
            except BaseException as error:
                replies.put((False, error))

        self._thread = threading.Thread(target=exchange, daemon=True, name="appworld-ipc")
        self._thread.start()
        try:
            ok, line = replies.get(timeout=self.timeout_seconds if timeout is None else timeout)
        except queue.Empty:
            self._raise(operation, "HostTimeout", timeout=True)
        if not ok or not line:
            # On Windows EOF may precede the process handle becoming signaled.
            # Preserve the actual exit status rather than winning that race
            # with TerminateProcess (which would replace it with code 1).
            try:
                self._process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass
            self._raise(operation, "WorkerExited")
        try:
            if not line.endswith(b"\n"):
                raise ValueError("Missing or oversized response line")
            response = json.loads(line.decode("utf-8"))
            if not isinstance(response, dict) or response.get("id") != request_id or type(response.get("ok")) is not bool:
                raise ValueError("Invalid protocol envelope")
            if not response["ok"]:
                error_type = response.get("error", {}).get("error_type")
                if not isinstance(error_type, str) or not error_type.isidentifier():
                    raise ValueError("Invalid error envelope")
                self._raise(operation, error_type)
            return response["result"]
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            self._raise(operation, "InvalidResponse")

    def execute(self, code):
        if not isinstance(code, str):
            raise TypeError("execute requires Python source text")
        result = self._request("execute", {"code": code})
        if not isinstance(result, str):
            self._raise("execute", "InvalidResponse")
        return result

    def evaluate(self):
        result = self._request("evaluate", {})
        if (not isinstance(result, dict) or type(result.get("success")) is not bool or
                type(result.get("score")) not in {int, float} or not math.isfinite(result["score"]) or
                not 0 <= result["score"] <= 1 or not isinstance(result.get("details"), dict)):
            self._raise("evaluate", "InvalidResponse")
        return result

    def close(self):
        if self.closed:
            return
        try:
            self._request("close", {})
            # The worker exits after acknowledging close; reap it normally.
            self._process.wait(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            self._raise("close", "HostTimeout", timeout=True)
        finally:
            self._stop()
