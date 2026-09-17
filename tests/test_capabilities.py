"""Query installed API metadata through the same isolated worker as MCP."""

from __future__ import annotations

from importlib import metadata
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest


class CapabilitiesTest(unittest.TestCase):
    def run_worker(self, request):
        with tempfile.TemporaryDirectory(prefix="kepler capabilities ") as directory:
            root = Path(directory)
            request_path, result_path = root / "request.json", root / "result.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, "-m", "kepler_formal_mcp.worker", str(request_path), str(result_path)],
                cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30, check=False,
            )
            self.assertTrue(result_path.is_file(), completed.stderr)
            return completed, json.loads(result_path.read_text(encoding="utf-8"))

    def test_info_reports_installed_api_without_design_inputs(self):
        completed, result = self.run_worker({"operation": "info"})
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["kepler_formal_version"], metadata.version("kepler-formal"))
        self.assertEqual(result["najaeda_version"], metadata.version("najaeda"))
        self.assertEqual(result["python_version"], platform.python_version())
        self.assertRegex(result["kepler_formal_git_hash"], r"^[0-9a-f]{7,40}$")
        self.assertEqual(result["enums"]["VerificationMode"], ["lec", "sec"])
        self.assertEqual(set(result["enums"]["Solver"]), {"kissat", "cadical", "glucose"})
        self.assertIn("k_induction", result["enums"]["SecEngine"])
        self.assertIn("dual_rail_steady", result["enums"]["SecEncoding"])
        self.assertIn("partially_proved", result["enums"]["VerificationStatus"])
        self.assertEqual(result["option_defaults"], {
            "mode": "lec", "solver": "kissat", "max_k": None,
            "sec_engine": None, "sec_encoding": None,
            "allow_boundary_mismatch": False, "report_skipped_outputs": False,
            "log_file": None, "log_level": None,
        })
        self.assertTrue({
            "status", "exit_code", "reason", "unproven_outputs", "skipped_observed_outputs",
            "equivalent", "conclusive", "coverage_percent",
        } <= set(result["result_fields"]))
        self.assertTrue({"verify_designs", "version", "git_hash", "NativeDesign", "from_najaeda"}
                        <= set(result["public_exports"]))
        self.assertIn("not transported by MCP", result["in_process_only"])

    def test_unknown_operation_returns_a_structured_worker_error(self):
        completed, result = self.run_worker({"operation": "missing"})
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(result["status"], "error")
        self.assertIn("ValueError: Unknown worker operation", result["reason"])

    def test_importing_capabilities_does_not_load_the_native_package(self):
        completed = subprocess.run(
            [sys.executable, "-c", (
                "import sys; import kepler_formal_mcp.capabilities; "
                "assert 'kepler_formal' not in sys.modules; "
                "assert 'najaeda' not in sys.modules"
            )],
            capture_output=True, text=True, timeout=30, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")


if __name__ == "__main__":
    unittest.main()
