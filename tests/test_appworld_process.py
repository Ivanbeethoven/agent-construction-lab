"""Real subprocess/protocol tests with a fake environment, NOT AppWorld results."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

from agent_construction_lab.fixed_model.appworld_process import (
    AppWorldProcessEnvironment, AppWorldProcessError, AppWorldProcessTimeout,
)


_FIXTURE = r'''
import json
import os
import sys
import time
from agent_construction_lab.fixed_model import environments
from agent_construction_lab.fixed_model.environment_worker import main

class FakeEnvironment:
    def __init__(self, task, *, experiment_name, **kwargs):
        self.mode = experiment_name
        if self.mode == "init_timeout":
            time.sleep(3600)
        if self.mode == "init_error":
            raise RuntimeError("PRIVATE_TASK_MARKER must only reach host stderr")
        # More than a pipe buffer: real stdout redirection and a temporary
        # stderr file must prevent a deadlock. Include an fd-level write.
        print("OFFICIAL_STYLE_NOISE:" + "n" * 100000)
        os.write(1, b"NATIVE_STDOUT_NOISE\n")
        self.instruction = json.dumps({"env": dict(os.environ), "task": task, "pid": os.getpid()})
        self.public_context = "Trusted protocol fixture, not a benchmark."
        self.value = 0

    def execute(self, code):
        if self.mode == "execute_timeout":
            time.sleep(3600)
        if self.mode == "execute_error":
            raise ValueError("PRIVATE_EXECUTION_MARKER")
        if self.mode == "execute_exit":
            os._exit(23)
        self.value += 1
        return "响应 " + code + " " + str(self.value)

    def evaluate(self):
        if self.mode == "evaluate_timeout":
            time.sleep(3600)
        if self.mode == "bad_evaluation":
            return {"success": "yes", "score": 1, "details": {}}
        return {"success": self.value == 2, "score": float(self.value == 2), "details": {"fixture_only": True}}

    def close(self):
        if self.mode == "close_timeout":
            time.sleep(3600)
        print("FAKE_WORLD_CLOSED")

# Test-only trusted code injection, never exposed by the worker wire protocol.
environments.AppWorldEnvironment = FakeEnvironment
raise SystemExit(main())
'''


class AppWorldProcessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.script = self.root / "fixture_worker.py"
        self.script.write_text(textwrap.dedent(_FIXTURE), encoding="utf-8")
        self.worlds = []

    def tearDown(self):
        for world in self.worlds:
            if not world.closed:
                world.close()
        self.directory.cleanup()

    def world(self, mode="normal", **options):
        parameters = {"appworld_root": str(self.root), "experiment_name": mode, "seed": 17,
                      "timeout_seconds": 0.25, "initialization_timeout_seconds": 3,
                      "_worker_argv": [sys.executable, "-u", str(self.script)]}
        parameters.update(options)
        result = AppWorldProcessEnvironment({"task_id": "fixture-1", "private_unused_payload": "MUST_NOT_BE_SENT"}, **parameters)
        self.worlds.append(result)
        return result

    def assertReaped(self, pid):
        self.assertIsInstance(pid, int)
        if os.name == "posix":
            # waitpid must report no child: Popen.wait() has already reaped it.
            with self.assertRaises(ChildProcessError):
                os.waitpid(pid, os.WNOHANG)
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def test_real_subprocess_protocol_state_unicode_and_stderr_pressure(self):
        world = self.world()
        self.assertEqual(world.execute("first"), "响应 first 1")
        self.assertEqual(world.execute("second"), "响应 second 2")
        self.assertEqual(world.evaluate(), {"success": True, "score": 1.0, "details": {"fixture_only": True}})
        self.assertEqual(json.loads(world.instruction)["task"], {"task_id": "fixture-1"})
        world.close()
        world.close()
        self.assertTrue(world.closed)
        self.assertTrue(world.stderr_truncated)
        self.assertIn("NATIVE_STDOUT_NOISE", world.stderr_tail)
        self.assertIn("FAKE_WORLD_CLOSED", world.stderr_tail)
        self.assertReaped(world.worker_pid)

    def test_environment_allowlist_drops_credentials_and_preserves_home(self):
        secrets = {"OPENAI_API_KEY": "fake-openai-secret", "ANTHROPIC_API_KEY": "fake-anthropic-secret",
                   "AWS_SECRET_ACCESS_KEY": "fake-cloud-secret", "AZURE_OPENAI_API_KEY": "fake-azure-secret",
                   "CUSTOM_SERVICE_CREDENTIAL": "fake-custom-secret", "HTTP_PROXY": "https://user:secret@proxy.invalid",
                   "APPWORLD_ROOT": "/wrong-root", "APPWORLD_CACHE": "/wrong-cache", "IPYTHONDIR": "/wrong-ipython"}
        old_home = os.environ.get("HOME")
        with patch.dict(os.environ, secrets):
            world = self.world()
        child = json.loads(world.instruction)["env"]
        for name in secrets:
            if name not in {"APPWORLD_ROOT", "APPWORLD_CACHE", "IPYTHONDIR"}:
                self.assertNotIn(name, child)
        self.assertEqual(child.get("HOME"), old_home)
        self.assertEqual(child["APPWORLD_ROOT"], str(self.root))
        for name in ("APPWORLD_CACHE", "IPYTHONDIR"):
            self.assertTrue(Path(child[name]).is_relative_to(self.root))
        self.assertEqual(os.environ.get("HOME"), old_home)
        self.assertNotIn("fake-openai-secret", world.instruction)

    def test_initialization_timeout_terminates_and_reaps_worker(self):
        started = time.monotonic()
        with self.assertRaises(AppWorldProcessTimeout) as caught:
            self.world("init_timeout", initialization_timeout_seconds=0.25)
        self.assertEqual(caught.exception.operation, "init")
        self.assertEqual(caught.exception.error_type, "HostTimeout")
        self.assertLess(time.monotonic() - started, 3)
        self.assertReaped(caught.exception.worker_pid)

    def test_each_runtime_operation_has_its_own_timeout_and_reaps(self):
        for operation in ("execute", "evaluate", "close"):
            with self.subTest(operation=operation):
                world = self.world(operation + "_timeout")
                started = time.monotonic()
                with self.assertRaises(AppWorldProcessTimeout) as caught:
                    getattr(world, operation)("trusted fixture code") if operation == "execute" else getattr(world, operation)()
                self.assertEqual(caught.exception.operation, operation)
                self.assertTrue(world.closed)
                self.assertLess(time.monotonic() - started, 3)
                self.assertReaped(world.worker_pid)

    def test_initialization_exception_is_safe_and_retains_host_diagnostics(self):
        with self.assertRaises(AppWorldProcessError) as caught:
            self.world("init_error")
        self.assertEqual(caught.exception.error_type, "RuntimeError")
        self.assertNotIn("PRIVATE_TASK_MARKER", str(caught.exception))
        self.assertIn("PRIVATE_TASK_MARKER", caught.exception.stderr_tail)
        self.assertIn("Traceback", caught.exception.stderr_tail)
        self.assertReaped(caught.exception.worker_pid)

    def test_execution_exception_does_not_become_task_feedback_or_leave_worker(self):
        world = self.world("execute_error")
        with self.assertRaises(AppWorldProcessError) as caught:
            world.execute("trusted fixture code")
        self.assertEqual(caught.exception.error_type, "ValueError")
        self.assertNotIn("PRIVATE_EXECUTION_MARKER", str(caught.exception))
        self.assertIn("PRIVATE_EXECUTION_MARKER", caught.exception.stderr_tail)
        self.assertTrue(world.closed)
        self.assertReaped(world.worker_pid)

    def test_worker_crash_is_an_exception_and_reaped(self):
        world = self.world("execute_exit")
        with self.assertRaises(AppWorldProcessError) as caught:
            world.execute("trusted fixture code")
        self.assertEqual(caught.exception.error_type, "WorkerExited")
        self.assertEqual(caught.exception.worker_returncode, 23)
        self.assertReaped(world.worker_pid)

    def test_invalid_evaluator_result_is_not_accepted_as_a_score(self):
        world = self.world("bad_evaluation")
        with self.assertRaises(AppWorldProcessError) as caught:
            world.evaluate()
        self.assertEqual(caught.exception.error_type, "InvalidResponse")
        self.assertReaped(world.worker_pid)

    def test_partial_protocol_line_cannot_bypass_the_deadline(self):
        broken = self.root / "partial_worker.py"
        broken.write_text("import sys,time\nsys.stdin.readline()\nsys.stdout.write('{')\nsys.stdout.flush()\ntime.sleep(3600)\n", encoding="utf-8")
        with self.assertRaises(AppWorldProcessTimeout) as caught:
            self.world(_worker_argv=[sys.executable, "-u", str(broken)], initialization_timeout_seconds=0.25)
        self.assertReaped(caught.exception.worker_pid)

    def test_large_request_write_cannot_block_outside_the_deadline(self):
        nonreader = self.root / "nonreader.py"
        nonreader.write_text("import json,sys,time\nr=json.loads(sys.stdin.readline())\nprint(json.dumps({'id':r['id'],'ok':True,'result':{'instruction':'fixture','public_context':'fixture'}}),flush=True)\ntime.sleep(3600)\n", encoding="utf-8")
        world = self.world(_worker_argv=[sys.executable, "-u", str(nonreader)])
        with self.assertRaises(AppWorldProcessTimeout):
            world.execute("x" * 2_000_000)
        self.assertReaped(world.worker_pid)

    def test_unknown_operation_cannot_supply_an_evaluator(self):
        # Exercise the actual packaged worker dispatcher before initialization;
        # no AppWorld installation or benchmark task is loaded.
        request = {"id": 1, "operation": "evaluate_callable", "payload": {"function": "private_reader"}}
        result = subprocess.run([sys.executable, "-u", "-m", "agent_construction_lab.fixed_model.environment_worker"],
                                input=json.dumps(request) + "\n", text=True,
                                capture_output=True, timeout=3, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 1)
        reply = json.loads(result.stdout)
        self.assertFalse(reply["ok"])
        self.assertNotIn("private_reader", result.stdout)
        self.assertEqual(reply["error"]["error_type"], "ValueError")


if __name__ == "__main__":
    unittest.main()
