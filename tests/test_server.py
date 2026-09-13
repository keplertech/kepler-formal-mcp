import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import yaml

import server
from test_results import HEADER, COVERAGE, FULL


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.a, self.b, self.lib = [self.root / name for name in ("a.v", "b.v", "cells.lib")]
        for path in (self.a, self.b, self.lib):
            path.write_text("unchanged input\n")
        self.binary = self.root / "kepler-formal"
        self.binary.write_text("#!/bin/sh\nexit 7\n")
        self.binary.chmod(0o755)
        self.env = patch.dict(os.environ, KEPLER_FORMAL_WORKSPACE=str(self.root),
                              KEPLER_FORMAL_AI_OUTPUT_DIR=str(self.root / "runs"),
                              KEPLER_FORMAL_BIN=str(self.binary))
        self.env.start()
        self.addCleanup(self.env.stop)

    def call(self, **kwargs):
        return server.verify_sec(str(self.a), str(self.b), [str(self.lib)], **kwargs)

    def fake_run(self, command, run, timeout):
        self.assertEqual(command[1], "--config")
        config = yaml.safe_load(Path(command[2]).read_text())
        self.assertEqual(config["verification"], "sec")
        self.assertTrue(config["report_skipped_pos"])
        self.assertFalse(config["cnf_export"])
        for value in config["input_paths"] + config["liberty_files"]:
            path = Path(value)
            self.assertTrue(path.is_relative_to(run / "inputs"))
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o444)
        Path(config["log_file"]).write_text(HEADER + COVERAGE + FULL)
        (run / "stdout.log").write_text("stdout evidence\n")
        (run / "stderr.log").write_text("")
        for name in server.SKIPPED_REPORTS:
            (run / name).write_text("")
        return 0, False

    def test_configured_binary_and_path_fallback(self):
        self.assertEqual(server._binary_path(), self.binary)
        with patch.dict(os.environ, KEPLER_FORMAL_BIN=""), patch.object(shutil, "which", return_value=str(self.binary)):
            self.assertEqual(server._binary_path(), self.binary)

    def test_invalid_explicit_binary_does_not_fallback(self):
        for name in ("relative", str(self.root / "absent"), str(self.a), str(self.root)):
            with patch.dict(os.environ, KEPLER_FORMAL_BIN=name), patch.object(shutil, "which") as which:
                self.assertEqual(self.call().status, "error")
                which.assert_not_called()

    def test_missing_binary_no_source_build(self):
        with patch.dict(os.environ, KEPLER_FORMAL_BIN=""), patch.object(shutil, "which", return_value=None), \
             patch.object(server, "_execute") as execute:
            result = self.call()
        self.assertIn("No source-build fallback", result.message)
        execute.assert_not_called()

    def test_fresh_runs_and_readonly_snapshots(self):
        with patch.object(server, "_execute", side_effect=self.fake_run):
            first, second = self.call(), self.call()
        self.assertEqual(first.status, "proved")
        self.assertNotEqual(first.run_directory, second.run_directory)
        self.assertEqual(first.input_hashes[str(self.a)], hashlib.sha256(self.a.read_bytes()).hexdigest())
        self.assertEqual(json.loads(Path(first.result_file).read_text())["status"], "proved")
        self.assertEqual(self.a.read_text(), "unchanged input\n")

    def test_concurrent_runs_do_not_share_files(self):
        with patch.object(server, "_execute", side_effect=self.fake_run), ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda _: self.call(), range(3)))
        self.assertEqual(len({result.run_directory for result in results}), 3)
        self.assertTrue(all(result.status == "proved" for result in results))

    def test_yaml_is_not_changed_and_relative_inputs_use_yaml_directory(self):
        config = self.root / "request.yaml"
        original = "input_paths: [a.v, b.v]\nliberty_files: [cells.lib]\n"
        config.write_text(original)
        config.chmod(0o444)
        with patch.object(server, "_execute", side_effect=self.fake_run):
            result = server.run_kepler_formal_yaml(str(config))
        self.assertEqual(result.status, "proved")
        self.assertEqual(config.read_text(), original)
        self.assertEqual((Path(result.run_directory) / "request.yaml").read_text(), original)

    def test_yaml_rejects_policy_bypasses_and_unknown_keys(self):
        config = self.root / "request.yaml"
        for extra in ({"verification": "lec"}, {"report_skipped_pos": False}, {"cnf_export": True},
                      {"dump_only": True}, {"scope": "module"}, {"max_k": True}, {"max_k": -1},
                      {"log_level": "critical"}, {"sec_engine": "bad"}, {"sec_encoding": "bad"},
                      {"format": "systemverilog"}, {"solver": "kissat\nverification: lec"},
                      {"log_file": "../a.v"}, {"log_file": str(self.a)}):
            data = {"input_paths": ["a.v", "b.v"], "liberty_files": ["cells.lib"], **extra}
            config.write_text(yaml.safe_dump(data))
            with self.subTest(extra=extra), patch.object(server, "_execute") as execute:
                self.assertEqual(server.run_kepler_formal_yaml(str(config)).status, "error")
                execute.assert_not_called()

    def test_yaml_malformed_duplicate_unsafe_or_oversize_rejected(self):
        config = self.root / "request.yaml"
        for text in ("- item", "[", "", "verification: sec\nverification: lec\n",
                     "!!python/object/apply:os.system ['false']", "#" * (server.MAX_YAML_BYTES + 1)):
            config.write_text(text)
            with self.subTest(text=text[:50]), patch.object(server, "_execute") as execute:
                self.assertEqual(server.run_kepler_formal_yaml(str(config)).status, "error")
                execute.assert_not_called()

    def test_output_dir_parameter_cannot_expand_host_permissions(self):
        for value in ("../escape", str(self.root), str(self.root.parent)):
            with self.subTest(value=value):
                self.assertEqual(self.call(allowed_output_dir=value).status, "error")
        with patch.object(server, "_execute", side_effect=self.fake_run):
            result = self.call(allowed_output_dir="child")
        self.assertTrue(Path(result.run_directory).is_relative_to(self.root / "runs/child"))

    def test_output_symlink_escape_is_rejected(self):
        runs = self.root / "runs"
        runs.mkdir()
        (runs / "escape").symlink_to(self.root)
        self.assertEqual(self.call(allowed_output_dir="escape").status, "error")

    def test_input_paths_must_be_inside_workspace(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "other.v"
            outside.write_text("module x; endmodule")
            (self.root / "alias.v").symlink_to(outside)
            for path in (outside, self.root / "alias.v"):
                self.assertEqual(server.verify_sec(str(path), str(self.b), []).status, "error")

    def test_executable_library_rejected(self):
        lib = self.root / "primitives.py"
        lib.write_text("raise RuntimeError('must not execute')")
        self.assertEqual(server.verify_sec(str(self.a), str(self.b), [str(lib)]).status, "error")

    def test_legacy_tool_defaults_to_sec_and_cnf_disabled(self):
        with patch.object(server, "_execute", side_effect=self.fake_run):
            result = server.create_yaml_and_run_kepler_formal([str(self.a), str(self.b)], [str(self.lib)])
        self.assertEqual(result.status, "proved")
        self.assertEqual(server.create_yaml_and_run_kepler_formal([str(self.a), str(self.b)], [], cnf_export=True).status, "error")

    def test_two_designs_required(self):
        for paths in ([], [str(self.a)], [str(self.a)] * 3):
            self.assertEqual(server.create_yaml_and_run_kepler_formal(paths, []).status, "error")

    def test_output_name_collisions_and_escapes_rejected(self):
        for name in (str(self.a), "../a.v", "inputs/a.v", "stdout.log", "result.json", "kepler.log",
                     "request.yaml", "boundary_terms.txt", "skipped_no_driver_pos.txt", "miter_log_0.txt"):
            self.assertEqual(server.create_yaml_and_run_kepler_formal([str(self.a), str(self.b)], [], yaml_output_path=name).status, "error")

    def test_missing_log_is_error_even_on_zero_exit(self):
        with patch.object(server, "_execute", return_value=(0, False)):
            result = self.call()
        self.assertEqual(result.status, "error")
        self.assertIn("fresh proof log", result.message)

    def test_nonempty_skipped_report_invalidates_full_proof(self):
        def run(command, directory, timeout):
            result = self.fake_run(command, directory, timeout)
            (directory / "skipped_multi_driver_pos.txt").write_text("Y[0]: multiple drivers\n")
            return result
        with patch.object(server, "_execute", side_effect=run):
            result = self.call()
        self.assertEqual(result.status, "error")
        self.assertIn("skipped-output", result.message)

    def test_missing_skipped_reports_are_not_zero_skipped_outputs(self):
        def incomplete(command, directory, timeout):
            self.fake_run(command, directory, timeout)
            (directory / "skipped_logical_loop_pos.txt").unlink()
            return 0, False
        with patch.object(server, "_execute", side_effect=incomplete):
            result = self.call()
        self.assertEqual(result.status, "error")
        self.assertIsNone(result.equivalent)
        self.assertTrue(result.blocking)
        self.assertIn("skipped-output reports", result.message)

    def test_changed_snapshot_invalidates_proof(self):
        def run(command, directory, timeout):
            result = self.fake_run(command, directory, timeout)
            target = next((directory / "inputs").glob("input_paths-*"))
            target.chmod(0o644)
            target.write_text("changed")
            return result
        with patch.object(server, "_execute", side_effect=run):
            self.assertEqual(self.call().status, "error")

    def test_actual_timeout_preserves_diagnostics(self):
        self.binary.write_text(f"#!{sys.executable}\nimport time\nprint('before timeout', flush=True)\ntime.sleep(30)\n")
        result = self.call(timeout_seconds=1)
        self.assertEqual(result.status, "error")
        self.assertTrue(result.timed_out)
        self.assertIn("before timeout", result.stdout_tail)
        self.assertTrue(Path(result.result_file).exists())

    def test_invalid_timeout_rejected_before_execution(self):
        for timeout in (0, -1, True, 3601):
            with patch.object(server, "_execute") as execute:
                self.assertEqual(self.call(timeout_seconds=timeout).status, "error")
                execute.assert_not_called()

    def test_actual_crash_is_not_an_inconclusive_proof(self):
        result = self.call()
        self.assertEqual(result.exit_code, 7)
        self.assertEqual(result.status, "error")
        self.assertTrue(result.blocking)

    def test_tail_is_bounded_full_log_retained(self):
        path = self.root / "large.log"
        path.write_text("x" * 100000 + "last message")
        self.assertEqual(len(server._tail(path)), 16384)
        self.assertTrue(server._tail(path).endswith("last message"))
        self.assertGreater(path.stat().st_size, 100000)

    def test_stdio_mcp_returns_structured_result(self):
        self.binary.write_text(f"#!{sys.executable}\nimport sys, yaml\nfrom pathlib import Path\nc=yaml.safe_load(Path(sys.argv[2]).read_text())\nassert c['verification']=='sec' and c['report_skipped_pos']\nPath(c['log_file']).write_text({(HEADER + COVERAGE + FULL)!r})\n")
        with self.binary.open("a") as stream:
            stream.write(f"for name in {sorted(server.SKIPPED_REPORTS)!r}: Path(name).write_text('')\n")

        async def exercise():
            params = StdioServerParameters(command=sys.executable,
                args=[str(Path(server.__file__).resolve())], env=dict(os.environ))
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual({tool.name for tool in tools.tools},
                                     {"verify_sec", "run_kepler_formal_yaml", "create_yaml_and_run_kepler_formal"})
                    for tool in tools.tools:
                        self.assertIsNotNone(tool.outputSchema)
                    response = await session.call_tool("verify_sec", {"golden": str(self.a), "revised": str(self.b), "liberty_files": [str(self.lib)]})
                    self.assertFalse(response.isError)
                    self.assertEqual(response.structuredContent["status"], "proved")
                    self.assertEqual(response.structuredContent["coverage"]["proved_outputs"], 18)
                    response = await session.call_tool("create_yaml_and_run_kepler_formal", {"input_paths": [str(self.a), str(self.b)], "liberty_files": [], "cnf_export": True})
                    self.assertEqual(response.structuredContent["status"], "error")
                    self.assertTrue(response.structuredContent["blocking"])
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
