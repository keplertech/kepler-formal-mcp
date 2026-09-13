"""Offline checks for using an installed Kepler-Formal executable."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import server


class InstalledBinaryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve()
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.environment = patch.dict(os.environ, {"PATH": str(self.bin_dir)}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def executable(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    def test_finds_nix_profile_executable_on_path(self):
        store_binary = self.executable(self.root / "store" / "release" / "bin" / "kepler-formal")
        profile_binary = self.bin_dir / "kepler-formal"
        profile_binary.symlink_to(store_binary)

        self.assertEqual(server._binary_path().resolve(), store_binary)

    def test_explicit_executable_overrides_path(self):
        self.executable(self.bin_dir / "kepler-formal")
        override = self.executable(self.root / "custom profile" / "bin" / "kepler-formal")
        os.environ["KEPLER_FORMAL_BINARY"] = str(override)

        self.assertEqual(server._binary_path().resolve(), override)

    def test_invalid_explicit_override_does_not_fall_back_to_path(self):
        self.executable(self.bin_dir / "kepler-formal")
        regular_file = self.root / "not-executable"
        regular_file.write_text("not an executable", encoding="utf-8")
        directory = self.root / "directory"
        directory.mkdir()

        for invalid in [self.root / "missing", regular_file, directory]:
            with self.subTest(override=str(invalid)):
                os.environ["KEPLER_FORMAL_BINARY"] = str(invalid)
                with self.assertRaises(FileNotFoundError):
                    server._binary_path()

    def test_local_build_trees_are_not_used_as_fallback(self):
        self.executable(self.root / "build" / "src" / "bin" / "kepler-formal")
        self.executable(self.root / "thirdparty" / "kepler-formal" / "build" / "src" / "bin" / "kepler-formal")

        with patch.object(server, "_workspace_root", return_value=self.root):
            with self.assertRaises(FileNotFoundError) as raised:
                server._binary_path()
        self.assertIn("install_kepler_formal.sh", str(raised.exception))


class RunInstalledBinaryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve()
        self.config = self.root / "config.yaml"
        self.log = self.root / "formal.log"
        self.config.write_text(f"log_file: {self.log}\n", encoding="utf-8")
        self.binary = self.root / "kepler-formal"
        self.binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.binary.chmod(0o755)
        self.environment = patch.dict(os.environ, {"KEPLER_FORMAL_BINARY": str(self.binary)}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def run_config(self, timeout=10):
        return server._run_from_yaml(self.config, timeout, [self.root])

    def assert_error_shape(self, result):
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["yaml_config"], str(self.config))
        self.assertIsInstance(result["exit_code"], int)
        self.assertIsInstance(result["stdout_tail"], str)
        self.assertIsInstance(result["stderr_tail"], str)

    def test_missing_binary_returns_structured_install_guidance(self):
        os.environ.pop("KEPLER_FORMAL_BINARY")
        os.environ["PATH"] = str(self.root / "missing-bin-directory")

        with patch.object(server.subprocess, "run") as run:
            result = self.run_config()

        run.assert_not_called()
        self.assert_error_shape(result)
        self.assertEqual(result["exit_code"], -1)
        self.assertIn("install_kepler_formal.sh", result["stderr_tail"])

    def test_invalid_binary_returns_structured_error(self):
        self.binary.chmod(0o644)

        with patch.object(server.subprocess, "run") as run:
            result = self.run_config()

        run.assert_not_called()
        self.assert_error_shape(result)
        self.assertEqual(result["exit_code"], -1)
        self.assertIn("KEPLER_FORMAL_BINARY", result["stderr_tail"])

    def test_launch_error_returns_structured_error(self):
        with patch.object(server.subprocess, "run", side_effect=OSError("exec format error")):
            result = self.run_config()

        self.assert_error_shape(result)
        self.assertEqual(result["exit_code"], -1)
        self.assertIn("exec format error", result["stderr_tail"])

    def test_timeout_returns_structured_error(self):
        with patch.object(server.subprocess, "run", side_effect=subprocess.TimeoutExpired([str(self.binary)], 10)):
            result = self.run_config(timeout=10)

        self.assert_error_shape(result)
        self.assertEqual(result["exit_code"], -1)
        self.assertIn("10 seconds", result["stderr_tail"])

    def test_success_uses_installed_binary_and_preserves_config_argument(self):
        self.log.write_text("formal output\n", encoding="utf-8")
        completed = subprocess.CompletedProcess([], 0, stdout="complete\n", stderr="")

        with patch.object(server.subprocess, "run", return_value=completed) as run:
            result = self.run_config(timeout=17)

        self.assertEqual(run.call_args.args[0], [str(self.binary), "--config", str(self.config)])
        self.assertEqual(run.call_args.kwargs["timeout"], 17)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["generated_log_file"], str(self.log))
        self.assertEqual(result["stdout_tail"], "complete")

    def test_nonzero_exit_code_is_not_reported_as_success(self):
        completed = subprocess.CompletedProcess([], 4, stdout="", stderr="bad configuration\n")
        with patch.object(server.subprocess, "run", return_value=completed):
            result = self.run_config()

        self.assert_error_shape(result)
        self.assertEqual(result["exit_code"], 4)
        self.assertEqual(result["stderr_tail"], "bad configuration")

    def test_missing_log_is_still_an_error(self):
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch.object(server.subprocess, "run", return_value=completed):
            result = self.run_config()

        self.assert_error_shape(result)
        self.assertIn("Expected log file was not created", result["stderr_tail"])


if __name__ == "__main__":
    unittest.main()
