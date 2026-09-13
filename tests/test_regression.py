"""Reject incomplete or contradictory evidence from a real MCP regression."""

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import run_nix_mcp_regression as regression
from scripts.run_nix_mcp_regression import validate_result


SEC_PROOF = (
    "No binary-defined difference was found. SEC proved equivalence under the "
    "dual-rail steady-state abstraction at k = 1."
)
SEC_COUNTEREXAMPLE = "Difference was found. SEC found a counterexample at k = 1."
FULL_COVERAGE = "SEC checked-output coverage: 100.00% (1/1 covered/existing outputs)."


class RegressionEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name).resolve()
        self.case_dir = self.root / "case"
        self.case_dir.mkdir()
        self.config = self.case_dir / "config.yaml"
        self.config.write_text("mode: sec\n", encoding="utf-8")
        self.log = self.case_dir / "kepler.log"

    def result(self, evidence, exit_code=0):
        self.log.write_text(evidence + "\n", encoding="utf-8")
        return {
            "status": "success" if exit_code == 0 else "error",
            "exit_code": exit_code,
            "yaml_config": str(self.config),
            "generated_log_file": str(self.log),
            "stdout_tail": "",
            "stderr_tail": "",
        }

    def validate(self, result, verdict="sec_equivalent", exit_code=0):
        return validate_result(result, verdict=verdict, exit_code=exit_code, case_dir=self.case_dir)

    def test_accepts_explicit_proof_and_expected_counterexample_with_full_coverage(self):
        proof = self.result(f"{SEC_PROOF}\n{FULL_COVERAGE}")
        self.assertIsInstance(self.validate(proof), dict)

        counterexample = self.result(f"{SEC_COUNTEREXAMPLE}\n{FULL_COVERAGE}", exit_code=3)
        self.assertIsInstance(self.validate(counterexample, "sec_counterexample", 3), dict)

    def test_complete_coverage_alone_does_not_prove_either_verdict(self):
        for verdict, exit_code in [("sec_equivalent", 0), ("sec_counterexample", 3)]:
            with self.subTest(verdict=verdict):
                result = self.result(FULL_COVERAGE, exit_code)
                with self.assertRaises(ValueError):
                    self.validate(result, verdict, exit_code)

    def test_sec_proof_requires_complete_uncontradicted_coverage(self):
        for coverage in [
            "",
            "SEC checked-output coverage: 0.00% (0/1 covered/existing outputs).",
            f"{FULL_COVERAGE}\nSEC checked-output coverage: 0.00% (0/1 covered/existing outputs).",
            f"{FULL_COVERAGE}\nSEC checked-output coverage: unavailable",
        ]:
            with self.subTest(coverage=coverage):
                result = self.result(f"{SEC_PROOF}\n{coverage}")
                with self.assertRaises(ValueError):
                    self.validate(result)

    def test_conflicting_proof_and_counterexample_cannot_satisfy_either_verdict(self):
        for verdict, exit_code in [("sec_equivalent", 0), ("sec_counterexample", 3)]:
            with self.subTest(verdict=verdict):
                result = self.result(f"{SEC_PROOF}\n{FULL_COVERAGE}", exit_code)
                result["stderr_tail"] = SEC_COUNTEREXAMPLE
                with self.assertRaises(ValueError):
                    self.validate(result, verdict, exit_code)

    def test_full_proof_text_cannot_hide_inconclusive_or_partial_check(self):
        for incomplete in ["SEC partially proved", "SEC was inconclusive", "SEC cannot run", "proof not run"]:
            with self.subTest(incomplete=incomplete):
                result = self.result(f"{SEC_PROOF}\n{FULL_COVERAGE}\n{incomplete}")
                with self.assertRaises(ValueError):
                    self.validate(result)

    def test_unknown_verdict_cannot_pass_on_coverage(self):
        result = self.result(FULL_COVERAGE)
        with self.assertRaises(ValueError):
            self.validate(result, verdict="sec_unknown")

    def test_expected_evidence_cannot_hide_process_or_mcp_failure(self):
        for field, value in [("status", "error"), ("exit_code", 3), ("exit_code", "0"), ("exit_code", False)]:
            with self.subTest(field=field, value=value):
                result = self.result(f"{SEC_PROOF}\n{FULL_COVERAGE}")
                result[field] = value
                with self.assertRaises(ValueError):
                    self.validate(result)

    def test_captured_output_cannot_replace_missing_or_empty_artifacts(self):
        evidence = f"{SEC_PROOF}\n{FULL_COVERAGE}"
        for artifact in ["missing log", "empty log", "missing config"]:
            with self.subTest(artifact=artifact):
                result = self.result(evidence)
                result["stdout_tail"] = evidence
                if artifact == "missing log":
                    result["generated_log_file"] = str(self.case_dir / "missing.log")
                elif artifact == "empty log":
                    self.log.write_text("", encoding="utf-8")
                else:
                    result["yaml_config"] = str(self.case_dir / "missing.yaml")
                with self.assertRaises(ValueError):
                    self.validate(result)

    def test_evidence_file_must_belong_to_this_case(self):
        result = self.result(f"{SEC_PROOF}\n{FULL_COVERAGE}")
        outside = self.root / "another-run.log"
        outside.write_text(self.log.read_text(encoding="utf-8"), encoding="utf-8")
        escaped = self.case_dir / "old-log.log"
        escaped.symlink_to(outside)

        for path in [outside, escaped]:
            with self.subTest(path=str(path)):
                result["generated_log_file"] = str(path)
                with self.assertRaises(ValueError):
                    self.validate(result)

    def test_lec_success_requires_explicit_uncontradicted_verdict(self):
        self.assertIsInstance(self.validate(self.result("No difference was found."), "lec_equivalent"), dict)

        for evidence in ["", "Difference was found.", "No difference was found.\nDifference was found."]:
            with self.subTest(evidence=evidence):
                with self.assertRaises(ValueError):
                    self.validate(self.result(evidence), "lec_equivalent")

    def test_existing_run_directory_is_rejected_before_starting_tools(self):
        original_config = self.config.read_bytes()
        with patch.object(regression.subprocess, "run") as subprocess_run:
            with patch.object(regression, "_call_tool") as call_tool:
                with self.assertRaises(FileExistsError):
                    asyncio.run(regression.run_regression(
                        binary=self.root / "unused-binary",
                        server=regression.ROOT / "server.py",
                        work_dir=self.case_dir,
                    ))

        subprocess_run.assert_not_called()
        call_tool.assert_not_called()
        self.assertEqual(self.config.read_bytes(), original_config)
        self.assertEqual(list(self.case_dir.iterdir()), [self.config])


if __name__ == "__main__":
    unittest.main()
