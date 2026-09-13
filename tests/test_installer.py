"""Exercise installer control flow with a fake Nix CLI, without downloads."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


INSTALLER = Path(__file__).resolve().parents[1] / "install_kepler_formal.sh"
BASH = shutil.which("bash")
PACKAGE = f"/nix/store/{'0' * 32}-kepler-formal-1.0.0"
REVISION = "0e0abf2aa6979e337aead8996983952dc1742530"
CACHE = "https://keplertech.cachix.org"

FAKE_NIX = r'''
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_NIX_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")

if "eval" in args:
    if any("builtins.currentSystem" in arg for arg in args):
        if os.environ.get("FAKE_SYSTEM_ERROR"):
            print(os.environ["FAKE_SYSTEM_ERROR"], file=sys.stderr)
            sys.exit(1)
        print(os.environ.get("FAKE_SYSTEM", "x86_64-linux"), end="")
    else:
        print(os.environ["FAKE_PACKAGE"], end="")
elif "path-info" in args:
    if "--offline" in args:
        sys.exit(0 if os.environ.get("FAKE_LOCAL") == "1" else 1)
    sys.exit(0 if os.environ.get("FAKE_CACHE", "1") == "1" else 1)
elif "profile" in args and "install" in args:
    sys.exit(int(os.environ.get("FAKE_INSTALL_STATUS", "0")))
else:
    print("unexpected Nix invocation", args, file=sys.stderr)
    sys.exit(99)
'''


@unittest.skipUnless(BASH, "installer tests require Bash")
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve()
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.nix = self.bin_dir / "nix"
        self.nix.write_text(f"#!{sys.executable}\n{FAKE_NIX}", encoding="utf-8")
        self.nix.chmod(0o755)
        self.log = self.root / "nix-calls.jsonl"
        self.environment = {
            "PATH": str(self.bin_dir),
            "HOME": str(self.root),
            "FAKE_NIX_LOG": str(self.log),
            "FAKE_PACKAGE": PACKAGE,
        }

    def run_installer(self, *args, **environment):
        return subprocess.run(
            [BASH, str(INSTALLER), *args],
            cwd=self.root,
            env={**self.environment, **environment},
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def install_calls(self):
        return [args for args in self.calls() if "profile" in args]

    def assert_subsequence(self, args, expected):
        size = len(expected)
        self.assertTrue(
            any(args[index:index + size] == expected for index in range(len(args) - size + 1)),
            f"Missing required options {expected!r} in {args!r}",
        )

    def assert_builds_disabled(self):
        self.assertTrue(self.calls())
        for args in self.calls():
            self.assert_subsequence(args, ["--max-jobs", "0"])
            self.assert_subsequence(args, ["--builders", ""])
            self.assert_subsequence(args, ["--option", "allow-import-from-derivation", "false"])
            self.assert_subsequence(args, ["--option", "accept-flake-config", "false"])
            self.assert_subsequence(args, ["--option", "require-sigs", "true"])
            self.assert_subsequence(args, ["--option", "fallback", "false"])

    def test_cached_package_installs_pinned_revision_with_builds_disabled(self):
        result = self.run_installer()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_builds_disabled()
        installs = self.install_calls()
        self.assertEqual(len(installs), 1)
        self.assertNotIn("--profile", installs[0])
        self.assertIn("--no-update-lock-file", installs[0])
        self.assertIn(
            f"git+https://github.com/keplertech/kepler-formal?rev={REVISION}&submodules=1#kepler-formal",
            installs[0],
        )
        remote_checks = [args for args in self.calls() if "path-info" in args and "--store" in args]
        self.assertEqual(len(remote_checks), 1)
        self.assert_subsequence(remote_checks[0], ["--store", CACHE, PACKAGE])
        self.assertIn(f"{PACKAGE}/bin/kepler-formal", result.stdout)

    def test_custom_profile_path_is_one_argument(self):
        profile = str(self.root / "profile with spaces")
        result = self.run_installer("--profile", profile, FAKE_SYSTEM="aarch64-darwin")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_subsequence(self.install_calls()[0], ["--profile", profile])
        self.assert_builds_disabled()

    def test_unpublished_package_fails_before_profile_mutation(self):
        result = self.run_installer(FAKE_CACHE="0")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source builds remain disabled", result.stderr)
        self.assertEqual(self.install_calls(), [])
        self.assert_builds_disabled()

    def test_existing_local_package_skips_remote_availability_check(self):
        result = self.run_installer(FAKE_LOCAL="1", FAKE_CACHE="0")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.install_calls()), 1)
        self.assertFalse(any("--store" in args for args in self.calls()))
        self.assert_builds_disabled()

    def test_unsupported_platform_stops_before_install(self):
        result = self.run_installer(FAKE_SYSTEM="aarch64-linux")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not support aarch64-linux", result.stderr)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.install_calls(), [])

    def test_nix_version_evaluation_error_stops_before_install(self):
        result = self.run_installer(FAKE_SYSTEM_ERROR="error: Nix 2.35+ is required.")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Nix 2.35+ is required", result.stderr)
        self.assertEqual(len(self.calls()), 1)
        self.assertEqual(self.install_calls(), [])

    def test_invalid_output_path_stops_before_cache_or_install(self):
        result = self.run_installer(FAKE_PACKAGE="/tmp/not-a-nix-store-output")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid package output path", result.stderr)
        self.assertFalse(any("path-info" in args or "profile" in args for args in self.calls()))

    def test_install_failure_has_no_source_build_fallback(self):
        result = self.run_installer(FAKE_INSTALL_STATUS="7")

        self.assertEqual(result.returncode, 7)
        self.assertIn("No source-build fallback was attempted", result.stderr)
        self.assertEqual(len(self.install_calls()), 1)
        self.assertEqual(self.calls()[-1], self.install_calls()[0])
        self.assert_builds_disabled()

    def test_missing_nix_returns_install_guidance(self):
        result = self.run_installer(PATH=str(self.root / "empty-bin"))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Nix 2.35+ is required", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_missing_profile_argument_stops_before_nix(self):
        result = self.run_installer("--profile")

        self.assertEqual(result.returncode, 2)
        self.assertIn("--profile requires a path", result.stderr)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
