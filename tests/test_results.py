import unittest

from formal_results import parse_sec_result


HEADER = "SEC engine: pdr\nSEC encoding: dual_rail_steady\n"
COVERAGE = "SEC checked-output coverage: 100.00% (18/18 covered/existing outputs).\n"
FULL = "SEC proved equivalence under the dual-rail steady-state abstraction at k = 6.\n"
PARTIAL = "SEC partially proved equivalence at k = 4: 8/18 outputs proved; remaining outputs are inconclusive.\n"
INCONCLUSIVE = "SEC was inconclusive up to max_k = 32: bound exhausted\n"
DIFFERENT = "Difference was found. SEC found a counterexample at k = 0.\n"


class ResultTests(unittest.TestCase):
    def parse(self, text=FULL, code=0, coverage=COVERAGE):
        return parse_sec_result(HEADER + coverage + text, code)

    def test_full_proof_and_assumptions(self):
        result = self.parse()
        self.assertEqual(result.status, "proved")
        self.assertTrue(result.equivalent)
        self.assertFalse(result.blocking)
        self.assertEqual(result.coverage.proved_outputs, 18)
        self.assertEqual(result.coverage.unproved_outputs, 0)
        self.assertEqual(result.coverage.skipped_outputs, 0)
        self.assertEqual(result.bound, 6)
        self.assertIn("binary-defined", result.warnings[0])

    def test_binary_full_proof(self):
        result = parse_sec_result("SEC engine: pdr\nSEC encoding: binary\n" + COVERAGE + "SEC proved equivalence at k = 1.\n", 0)
        self.assertEqual(result.status, "proved")
        self.assertEqual(result.warnings, [])

    def test_partial_is_warning_not_equivalence(self):
        result = self.parse(PARTIAL, 1)
        self.assertEqual(result.status, "partial")
        self.assertIsNone(result.equivalent)
        self.assertFalse(result.blocking)
        self.assertEqual(result.coverage.checked_outputs, 18)
        self.assertEqual(result.coverage.proved_outputs, 8)
        self.assertEqual(result.coverage.unproved_outputs, 10)

    def test_inconclusive_is_warning_not_mismatch(self):
        for text in (INCONCLUSIVE, INCONCLUSIVE.replace("up to", "before completing")):
            result = self.parse(text, 2)
            self.assertEqual(result.status, "inconclusive")
            self.assertIsNone(result.equivalent)
            self.assertFalse(result.blocking)
            self.assertIsNone(result.coverage.proved_outputs)

    def test_counterexample_is_blocking(self):
        result = self.parse(DIFFERENT, 3)
        self.assertEqual(result.status, "counterexample")
        self.assertFalse(result.equivalent)
        self.assertTrue(result.blocking)

    def test_exit_code_alone_never_proves_equivalence(self):
        for code in (0, 1, 2, 3, -9):
            self.assertEqual(self.parse("No difference was found.\n", code).status, "error")

    def test_verdict_exit_code_must_agree(self):
        for text, expected in ((FULL, 0), (PARTIAL, 1), (INCONCLUSIVE, 2), (DIFFERENT, 3)):
            for code in (0, 1, 2, 3, -9):
                if code != expected:
                    with self.subTest(text=text, code=code):
                        self.assertEqual(self.parse(text, code).status, "error")

    def test_missing_invalid_ambiguous_coverage_fails(self):
        for coverage in ("", COVERAGE * 2, COVERAGE.replace("18/18", "0/0"),
                         COVERAGE.replace("18/18", "0/18"), COVERAGE.replace("18/18", "19/18"),
                         COVERAGE.replace("100.00", "99.00"), COVERAGE.replace("100.00", "1.2.3")):
            with self.subTest(coverage=coverage):
                self.assertEqual(self.parse(coverage=coverage).status, "error")

    def test_proved_checked_subset_is_only_partial(self):
        result = self.parse(coverage=COVERAGE.replace("100.00% (18/18", "44.44% (8/18"))
        self.assertEqual(result.status, "partial")
        self.assertIsNone(result.equivalent)
        self.assertEqual(result.coverage.proved_outputs, 8)
        self.assertEqual(result.coverage.skipped_outputs, 10)

    def test_conflicting_verdicts_fail(self):
        for extra in (FULL, PARTIAL, DIFFERENT, INCONCLUSIVE, "SEC verification did not prove all observed outputs.",
                      "SEC skipped observed outputs due to extraction or coverage limitations:"):
            self.assertEqual(self.parse(FULL + extra).status, "error")

    def test_tool_and_extraction_failures_are_not_warnings(self):
        for extra in ("SEC cannot run on this design pair", "[critical] parse failure", "[error] failed",
                      "SEC BTOR2 exported; proof not run"):
            result = self.parse(INCONCLUSIVE + extra, 2)
            self.assertEqual(result.status, "error")
            self.assertTrue(result.blocking)

    def test_partial_counts_must_be_consistent(self):
        for text in (PARTIAL.replace("8/18", "0/18"), PARTIAL.replace("8/18", "19/18"),
                     PARTIAL.replace("8/18", "8/17"), PARTIAL.replace("8/18", "18/18")):
            self.assertEqual(self.parse(text, 1).status, "error")

    def test_proof_progress_is_not_checked_coverage(self):
        result = self.parse("SEC PDR proven outputs: 8/18\n" + PARTIAL, 1)
        self.assertEqual(result.coverage.proved_outputs, 8)
        self.assertEqual(result.coverage.checked_outputs, 18)
        for progress in ("7/18", "19/18", "8/17"):
            self.assertEqual(self.parse(f"SEC PDR proven outputs: {progress}\n" + PARTIAL, 1).status, "error")

    def test_inconclusive_progress_preserved(self):
        result = self.parse("SEC PDR proven outputs: 0/18\n" + INCONCLUSIVE, 2)
        self.assertEqual(result.coverage.proved_outputs, 0)


if __name__ == "__main__":
    unittest.main()
