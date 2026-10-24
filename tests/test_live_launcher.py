"""Credential-boundary tests: temporary SQLite, fake secrets, no WSL or network.

These tests never open the user's CC Switch database or read actual API keys.
Every child process, model execution, and AppWorld operation is intercepted.
"""
from contextlib import ExitStack, closing, redirect_stderr, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
FAKE_KEY = "sk-LOCAL-UNIT-TEST-ONLY-NEVER-A-REAL-KEY"
FAKE_PREVIOUS = "prior-UNIT-TEST-credential"
TEST_ENV = "ACL_LAUNCHER_UNIT_TEST_KEY"
ENDPOINT = "https://api.deepseek.com/v1"


def _script(name):
    spec = importlib.util.spec_from_file_location("acl_test_" + name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


launcher = _script("run_with_ccswitch")
live = _script("run_appworld_live")


def _snapshot(root):
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


class CredentialFixtures(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="acl-launcher-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.model_path = self.root / "model.json"
        self.model = {"model": "test-fixed-model", "model_snapshot": "test-fixed-model",
                      "expected_response_model": "test-fixed-model", "base_url": ENDPOINT,
                      "api_key_env": TEST_ENV}
        self.model_path.write_text(json.dumps(self.model), encoding="utf-8")
        # A safety backstop in addition to the narrower process/client mocks.
        for target, message in (("socket.create_connection", "Network is forbidden in credential tests"),
                                ("subprocess.Popen", "Unmocked subprocess is forbidden")):
            guard = patch(target, side_effect=AssertionError(message))
            guard.start()
            self.addCleanup(guard.stop)

    def assertNoPersistedSecret(self):
        for path in self.root.rglob("*"):
            if path.is_file() and path.suffix != ".db":
                self.assertNotIn(FAKE_KEY.encode(), path.read_bytes(), str(path))


class CCSwitchLauncherTests(CredentialFixtures):
    def make_database(self, *, endpoint=ENDPOINT, key=FAKE_KEY, duplicate=False):
        database = self.root / "fixture.db"
        with closing(sqlite3.connect(database)) as connection:
            with connection:
                connection.execute("CREATE TABLE providers (id TEXT, app_type TEXT, settings_config TEXT)")
                row = ("deepseek", "opencode", json.dumps({"options": {"baseURL": endpoint, "apiKey": key}}))
                connection.execute("INSERT INTO providers VALUES (?, ?, ?)", row)
                if duplicate:
                    connection.execute("INSERT INTO providers VALUES (?, ?, ?)", row)
        return database

    def invoke(self, database, *, conversion_output=b"/fixture/workspace\n", conversion_returncode=0, **options):
        argv = ["run_with_ccswitch.py", "--database", str(database), "--distribution", "Fixture-Ubuntu",
                "--python", "/fixture/venv/bin/python", "--model-config", "model.json",
                "--appworld-root", "/fixture/data", "--out", "runs/local-live/fixture"]
        for key, value in options.items():
            argv.extend(["--" + key.replace("_", "-"), value])
        real_connect = sqlite3.connect
        expected_uri = database.resolve().as_uri() + "?mode=ro"
        connections = []

        def connect(database_uri, **kwargs):
            # Fails before any SQLite access if code reaches an unintended DB.
            self.assertEqual(database_uri, expected_uri)
            self.assertEqual(kwargs, {"uri": True})
            connection = real_connect(database_uri, **kwargs)
            connections.append(connection)
            return connection

        stdout, stderr = io.StringIO(), io.StringIO()
        convert = Mock(return_value=subprocess.CompletedProcess([], conversion_returncode,
            conversion_output, b"PRIVATE-WSL-DIAGNOSTIC"))
        run = Mock(return_value=subprocess.CompletedProcess([], 7))

        def subprocess_dispatch(command, **kwargs):
            if "wslpath" in command:
                self.assertEqual(kwargs, {"capture_output": True})
                return convert(command, **kwargs)
            return run(command, **kwargs)

        with ExitStack() as stack:
            stack.enter_context(patch.object(launcher, "__file__", str(self.root / "scripts" / "run_with_ccswitch.py")))
            stack.enter_context(patch("sys.argv", argv))
            stack.enter_context(patch.object(launcher.sqlite3, "connect", side_effect=connect))
            stack.enter_context(patch.object(launcher.subprocess, "run", side_effect=subprocess_dispatch))
            stack.enter_context(redirect_stdout(stdout))
            stack.enter_context(redirect_stderr(stderr))
            try:
                result = launcher.main()
            except Exception as error:
                result = error
            finally:
                # Cleanup is explicit even if a script error interrupts its
                # normal resource management; only our temporary DB is touched.
                for connection in connections:
                    connection.close()
        self.assertNotIn(FAKE_KEY, stdout.getvalue() + stderr.getvalue())
        self.assertNotIn("PRIVATE-WSL-DIAGNOSTIC", stdout.getvalue() + stderr.getvalue())
        return result, convert, run

    def test_key_uses_only_child_stdin_and_database_is_read_only(self):
        database = self.make_database()
        before = _snapshot(self.root)
        result, convert, run = self.invoke(database, manifest="manifest.json", manifest_sha256="a" * 64, study_config="study.json")
        self.assertEqual(result, 7)
        convert.assert_called_once()
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["wsl", "--distribution", "Fixture-Ubuntu"])
        self.assertIn("--credentials-stdin", command)
        self.assertEqual(command[-6:], ["--manifest", "manifest.json", "--manifest-sha256", "a" * 64,
                                        "--study-config", "study.json"])
        self.assertNotIn(FAKE_KEY, repr(command))
        self.assertNotIn(FAKE_KEY, repr(convert.call_args))
        self.assertEqual(json.loads(run.call_args.kwargs["input"]), {"api_key": FAKE_KEY})
        self.assertIs(run.call_args.kwargs["text"], True)
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertNotIn("env", run.call_args.kwargs)
        self.assertEqual(before, _snapshot(self.root), "Launcher must not persist credentials or mutate the database")
        self.assertNoPersistedSecret()

    def test_wsl_utf8_and_utf16_notices_do_not_change_stdin_credential_boundary(self):
        database = self.make_database()
        before = _snapshot(self.root)
        for encoding in ("utf-8", "utf-16-le"):
            with self.subTest(encoding=encoding):
                output = "WSL 提示：测试信息\n/fixture/workspace\n".encode(encoding)
                result, convert, run = self.invoke(database, conversion_output=output)
                self.assertEqual(result, 7)
                command = run.call_args.args[0]
                self.assertEqual(command[command.index("--cd") + 1], "/fixture/workspace")
                self.assertNotIn(FAKE_KEY, repr(command))
                self.assertNotIn(FAKE_KEY, repr(convert.call_args))
                self.assertEqual(json.loads(run.call_args.kwargs["input"]), {"api_key": FAKE_KEY})
        self.assertEqual(before, _snapshot(self.root))

    def test_continuation_anchor_is_forwarded_without_credentials_in_argv(self):
        database = self.make_database()
        result, _, run = self.invoke(database, resume_from="runs/local-live/old", resume_anchor="b" * 64)
        self.assertEqual(result, 7)
        command = run.call_args.args[0]
        self.assertEqual(command[-4:], ["--resume-from", "runs/local-live/old", "--resume-anchor", "b" * 64])
        self.assertNotIn(FAKE_KEY, repr(command))

    def test_calibration_24_step_limit_is_forwarded_without_changing_credential_boundary(self):
        database = self.make_database()
        before = _snapshot(self.root)
        result, convert, run = self.invoke(database, calibration_task="fixture_train_1", calibration_max_steps="24")
        self.assertEqual(result, 7)
        convert.assert_called_once()
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[-4:], ["--calibration-task", "fixture_train_1", "--calibration-max-steps", "24"])
        self.assertNotIn(FAKE_KEY, repr(command))
        self.assertEqual(json.loads(run.call_args.kwargs["input"]), {"api_key": FAKE_KEY})
        self.assertEqual(before, _snapshot(self.root))

    def test_calibration_step_limits_0_and_101_are_rejected_before_database_access(self):
        for steps in (0, 101):
            with self.subTest(steps=steps):
                argv = ["run_with_ccswitch.py", "--database", str(self.root / "never-open.db"),
                        "--distribution", "Fixture-Ubuntu", "--python", "/fixture/python",
                        "--model-config", "never-read.json", "--appworld-root", "/fixture/data",
                        "--out", "runs/local-live/fixture", "--calibration-max-steps", str(steps)]
                with patch("sys.argv", argv), redirect_stderr(io.StringIO()), \
                        patch.object(launcher.sqlite3, "connect") as connect, \
                        patch.object(launcher.subprocess, "run") as child, \
                        patch.object(Path, "read_text") as read_config:
                    with self.assertRaises(SystemExit) as raised:
                        launcher.main()
                self.assertEqual(raised.exception.code, 2)
                read_config.assert_not_called()
                connect.assert_not_called()
                child.assert_not_called()

    def test_wslpath_failure_or_invalid_output_never_starts_credential_child(self):
        database = self.make_database()
        cases = [(b"/fixture/workspace\n", 1), (b"relative/path\n", 0), (b"", 0),
                 (b"\xff", 0), (b"/fixture\x00workspace\n", 0)]
        for output, returncode in cases:
            with self.subTest(output=repr(output), returncode=returncode):
                result, convert, run = self.invoke(database, conversion_output=output, conversion_returncode=returncode)
                self.assertIsInstance(result, (RuntimeError, ValueError))
                self.assertNotIn(FAKE_KEY, str(result))
                self.assertNotIn("PRIVATE-WSL-DIAGNOSTIC", str(result))
                convert.assert_called_once()
                run.assert_not_called()

    def test_endpoint_mismatch_prevents_even_wslpath_launch(self):
        database = self.make_database()
        self.model["base_url"] = "https://different-provider.invalid/v1"
        self.model_path.write_text(json.dumps(self.model), encoding="utf-8")
        before = _snapshot(self.root)
        result, convert, run = self.invoke(database)
        self.assertIsInstance(result, ValueError)
        self.assertNotIn(FAKE_KEY, str(result))
        convert.assert_not_called()
        run.assert_not_called()
        self.assertEqual(before, _snapshot(self.root))

    def test_agreeing_nonofficial_endpoint_is_still_rejected_before_child(self):
        malicious = "https://untrusted.invalid/v1"
        database = self.make_database(endpoint=malicious)
        self.model["base_url"] = malicious
        self.model_path.write_text(json.dumps(self.model), encoding="utf-8")
        result, convert, run = self.invoke(database)
        self.assertIsInstance(result, ValueError)
        convert.assert_not_called()
        run.assert_not_called()

    def test_ambiguous_provider_or_missing_key_prevents_child(self):
        for case in ("duplicate", "empty"):
            with self.subTest(case=case):
                database = self.make_database(duplicate=case == "duplicate", key="" if case == "empty" else FAKE_KEY)
                result, convert, run = self.invoke(database)
                self.assertIsInstance(result, ValueError)
                convert.assert_not_called()
                run.assert_not_called()
                database.unlink()


class AppWorldLiveCredentialTests(CredentialFixtures):
    def invoke(self, *, initial_environment=None, credential=None, readiness=None,
               doctor_error=None, study_error=None, study=False, calibration_max_steps=None):
        fake_environment = dict(initial_environment or {})
        argv = ["run_appworld_live.py", "--credentials-stdin", "--model-config", str(self.model_path),
                "--appworld-root", "/fixture/data", "--out", str(self.root / "run")]
        if calibration_max_steps is not None:
            argv += ["--calibration-max-steps", str(calibration_max_steps)]
        if study:
            manifest = self.root / "manifest.json"
            study_config = self.root / "study.json"
            manifest.write_text(json.dumps({"fixture": True}), encoding="utf-8")
            study_config.write_text(json.dumps({"methods": ["G"]}), encoding="utf-8")
            argv += ["--manifest", str(manifest), "--study-config", str(study_config)]
        input_text = json.dumps({"api_key": FAKE_KEY} if credential is None else credential)
        seen = []

        def check_readiness(*args):
            seen.append(dict(fake_environment))
            if doctor_error:
                raise doctor_error
            return readiness if readiness is not None else {"ready_for_live_smoke": False, "live_model_probed": False}

        def run_study(*args, **kwargs):
            seen.append(dict(fake_environment))
            self.assertNotIn(FAKE_KEY, json.dumps(args[1].to_dict()))
            if study_error:
                raise study_error
            return {"completed": True, "actual_episode_counts": {"query": 1}, "total_actual_usage": {"model_calls": 0}}

        stdout, stderr = io.StringIO(), io.StringIO()
        before = _snapshot(self.root)
        with ExitStack() as stack:
            stack.enter_context(patch.object(live.os, "environ", fake_environment))
            stack.enter_context(patch.object(live, "__file__", str(self.root / "scripts" / "run_appworld_live.py")))
            stack.enter_context(patch("sys.argv", argv))
            stack.enter_context(patch("sys.stdin", io.StringIO(input_text)))
            doctor = stack.enter_context(patch.object(live, "doctor", side_effect=check_readiness))
            study_mock = stack.enter_context(patch.object(live, "run_study", side_effect=run_study))
            revision = stack.enter_context(patch.object(live.subprocess, "run",
                return_value=subprocess.CompletedProcess(["git", "rev-parse", "HEAD"], 0, "fixture-revision\n", "")))
            stack.enter_context(patch.object(live, "OpenAICompatibleClient", side_effect=AssertionError("Unexpected model client")))
            stack.enter_context(patch.object(live, "create_environment", side_effect=AssertionError("Unexpected AppWorld")))
            stack.enter_context(patch.object(live, "run_episode", side_effect=AssertionError("Unexpected episode")))
            stack.enter_context(patch.object(live, "write_json", side_effect=AssertionError("Unexpected credential persistence")))
            stack.enter_context(redirect_stdout(stdout))
            stack.enter_context(redirect_stderr(stderr))
            try:
                result = live.main()
            except (Exception, SystemExit) as error:
                result = error
        self.assertNotIn(FAKE_KEY, stdout.getvalue() + stderr.getvalue())
        self.assertNotIn(FAKE_KEY, repr(revision.call_args_list))
        self.assertEqual(before, _snapshot(self.root))
        self.assertNoPersistedSecret()
        return result, fake_environment, seen, doctor, study_mock

    def test_doctor_failure_restores_existing_environment_credential(self):
        previous = {TEST_ENV: FAKE_PREVIOUS, "UNRELATED_TEST_SETTING": "unchanged"}
        result, restored, seen, doctor, study = self.invoke(initial_environment=previous)
        self.assertEqual(result, 1)
        self.assertEqual(seen[0][TEST_ENV], FAKE_KEY)
        self.assertEqual(restored, previous)
        doctor.assert_called_once()
        study.assert_not_called()

    def test_calibration_24_step_limit_reaches_readiness_without_running_an_episode(self):
        result, restored, seen, doctor, study = self.invoke(calibration_max_steps=24)
        self.assertEqual(result, 1)
        self.assertEqual(restored, {})
        self.assertEqual(seen[0][TEST_ENV], FAKE_KEY)
        doctor.assert_called_once()
        study.assert_not_called()

    def test_calibration_step_limits_0_and_101_are_rejected_before_credentials_or_execution(self):
        previous = {TEST_ENV: FAKE_PREVIOUS}
        for steps in (0, 101):
            with self.subTest(steps=steps):
                result, restored, seen, doctor, study = self.invoke(
                    initial_environment=previous, calibration_max_steps=steps)
                self.assertIsInstance(result, SystemExit)
                self.assertEqual(result.code, 2)
                self.assertEqual(restored, previous)
                self.assertEqual(seen, [])
                doctor.assert_not_called()
                study.assert_not_called()

    def test_doctor_failure_removes_previously_absent_environment_credential(self):
        result, restored, seen, _, study = self.invoke(initial_environment={"UNRELATED_TEST_SETTING": "unchanged"})
        self.assertEqual(result, 1)
        self.assertEqual(seen[0][TEST_ENV], FAKE_KEY)
        self.assertNotIn(TEST_ENV, restored)
        self.assertEqual(restored, {"UNRELATED_TEST_SETTING": "unchanged"})
        study.assert_not_called()

    def test_raised_doctor_error_also_restores_prior_environment(self):
        expected = RuntimeError("fixture doctor failed")
        result, restored, seen, _, study = self.invoke(initial_environment={TEST_ENV: FAKE_PREVIOUS}, doctor_error=expected)
        self.assertIs(result, expected)
        self.assertEqual(seen[0][TEST_ENV], FAKE_KEY)
        self.assertEqual(restored, {TEST_ENV: FAKE_PREVIOUS})
        study.assert_not_called()

    def test_malformed_secret_rejected_before_readiness_or_execution(self):
        invalid = ({"api_key": ""}, {"api_key": "  "}, {"api_key": "fake\nsecret"},
                   {"api_key": "fake\rsecret"}, {"api_key": 12}, {"api_key": FAKE_KEY, "extra": 1}, [FAKE_KEY])
        for secret in invalid:
            with self.subTest(secret_type=type(secret).__name__):
                result, restored, seen, doctor, study = self.invoke(initial_environment={TEST_ENV: FAKE_PREVIOUS}, credential=secret)
                self.assertIsInstance(result, ValueError)
                self.assertNotIn(FAKE_KEY, str(result))
                self.assertEqual(restored, {TEST_ENV: FAKE_PREVIOUS})
                self.assertEqual(seen, [])
                doctor.assert_not_called()
                study.assert_not_called()

    def test_credential_input_requires_named_environment_variable(self):
        self.model["api_key_env"] = None
        self.model_path.write_text(json.dumps(self.model), encoding="utf-8")
        result, restored, seen, doctor, study = self.invoke()
        self.assertIsInstance(result, SystemExit)
        self.assertEqual(result.code, 2)
        self.assertEqual(restored, {})
        self.assertEqual(seen, [])
        doctor.assert_not_called()
        study.assert_not_called()

    def test_mocked_successful_study_never_serializes_key_and_restores_environment(self):
        previous = {TEST_ENV: FAKE_PREVIOUS}
        result, restored, seen, _, study = self.invoke(initial_environment=previous,
            readiness={"ready_for_live_smoke": True}, study=True)
        self.assertEqual(result, 0)
        self.assertEqual(restored, previous)
        self.assertEqual([item[TEST_ENV] for item in seen], [FAKE_KEY, FAKE_KEY])
        study.assert_called_once()

    def test_mocked_study_exception_restores_environment(self):
        expected = RuntimeError("fixture study failed")
        result, restored, seen, _, study = self.invoke(initial_environment={TEST_ENV: FAKE_PREVIOUS},
            readiness={"ready_for_live_smoke": True}, study=True, study_error=expected)
        self.assertIs(result, expected)
        self.assertEqual(restored, {TEST_ENV: FAKE_PREVIOUS})
        self.assertEqual([item[TEST_ENV] for item in seen], [FAKE_KEY, FAKE_KEY])
        study.assert_called_once()


if __name__ == "__main__":
    unittest.main()
