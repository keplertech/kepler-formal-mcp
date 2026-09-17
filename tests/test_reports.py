"""Keep native skipped-output diagnostics available after worker cleanup."""

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from kepler_formal_mcp import config, runner


class NativeReportTests(unittest.TestCase):
    def test_published_library_returns_nonempty_no_driver_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            liberty = root / "cells.lib"
            liberty.write_text('''library(test) {
  cell(BUF) {
    pin(A) { direction : input; }
    pin(Y) { direction : output; function : "A"; }
  }
}
''')
            reference = root / "reference.v"
            reference.write_text('''module top(input a, input b, output good, output no_driver);
  wire undriven;
  BUF g(.A(a), .Y(good));
  BUF f(.A(undriven), .Y(no_driver));
endmodule
''')
            candidate = root / "candidate.v"
            candidate.write_text('''module top(input a, input b, output good, output no_driver);
  BUF g(.A(b), .Y(good));
  BUF f(.A(a), .Y(no_driver));
endmodule
''')
            yaml_path = root / "request.yaml"
            request = config.normalize({
                "input_paths": [str(reference), str(candidate)],
                "liberty_files": [str(liberty)],
                "report_skipped_outputs": True,
            }, yaml_path, root)

            result = runner.run(request, yaml_path, 30)

            self.assertEqual("success", result["status"], result)
            self.assertEqual("different", result["verdict"], result)
            report = result["reports"]["skipped_no_driver_pos.txt"]
            self.assertIn("no_driver", report)
            self.assertIn("no drivers", report)
            self.assertEqual([], list(root.glob("skipped*.txt")))

    def test_reports_survive_worker_cleanup_without_copying_arbitrary_files(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            worker_directories = []

            def worker(command, **kwargs):
                work = Path(kwargs["cwd"])
                worker_directories.append(work)
                (work / "skipped_multi_driver_pos.txt").write_text("conflict: multiple drivers\n")
                (work / "skipped_no_driver_pos.txt").write_text("floating: no driver\n")
                (work / "skipped_logical_loop_pos.txt").write_text("feedback: logical loop\n")
                (work / "unrelated.txt").write_text("not a report")
                Path(command[-1]).write_text(json.dumps({"status": "equivalent", "exit_code": 0}))
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(runner.subprocess, "run", side_effect=worker):
                result = runner.run({"log_file": str(output / "verify.log")}, output / "request.yaml", 30)

            self.assertEqual({
                "skipped_multi_driver_pos.txt": "conflict: multiple drivers\n",
                "skipped_no_driver_pos.txt": "floating: no driver\n",
                "skipped_logical_loop_pos.txt": "feedback: logical loop\n",
            }, result["reports"])
            self.assertFalse(worker_directories[0].exists())
            self.assertEqual([], list(output.iterdir()))

    def test_partial_reports_survive_a_worker_timeout(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)

            def worker(command, **kwargs):
                work = Path(kwargs["cwd"])
                (work / "skipped_no_driver_pos.txt").write_text("floating: no driver\n")
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])

            with patch.object(runner.subprocess, "run", side_effect=worker):
                result = runner.run({"log_file": str(output / "verify.log")}, output / "request.yaml", 30)

            self.assertEqual("error", result["status"])
            self.assertEqual({"skipped_no_driver_pos.txt": "floating: no driver\n"}, result["reports"])

    def test_directories_are_not_read_as_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "skipped_multi_driver_pos.txt").mkdir()
            self.assertEqual({}, runner.read_reports(root))

    def test_report_symlinks_are_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.txt"
            outside.write_text("outside worker workspace")
            work = root / "worker"
            work.mkdir()
            try:
                (work / "skipped_multi_driver_pos.txt").symlink_to(outside)
            except (NotImplementedError, OSError):
                self.skipTest("Creating symlinks is unavailable")
            self.assertEqual({}, runner.read_reports(work))


if __name__ == "__main__":
    unittest.main()
