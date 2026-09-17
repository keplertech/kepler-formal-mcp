"""Exercise the MCP tools against the installed Kepler Formal Python package."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
import yaml

from kepler_formal_mcp import server


PASS_THROUGH = "module top(input a, output y); assign y = a; endmodule\n"
CONSTANT_OUTPUT = "module top(input a, output y); assign y = 1'b0; endmodule\n"
LIBERTY = """
library(test_cells) {
  cell(BUF) {
    pin(A) { direction : input; }
    pin(Y) { direction : output; function : "A"; }
  }
  cell(INV) {
    pin(A) { direction : input; }
    pin(Y) { direction : output; function : "!A"; }
  }
}
"""


@contextmanager
def working_directory(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class ToolTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="kepler mcp test ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.designs = self.root / "designs"
        self.designs.mkdir()
        self.outputs = self.root / "outputs"
        self.outputs.mkdir()
        self.reference = self.write_design("reference.v", PASS_THROUGH)
        self.candidate = self.write_design("candidate.v", PASS_THROUGH)

    def write_design(self, name, text):
        path = self.designs / name
        path.write_text(text, encoding="utf-8")
        return path

    def create(self, **overrides):
        arguments = {
            "input_paths": [str(self.reference), str(self.candidate)],
            "liberty_files": [],
            "yaml_output_path": str(self.outputs / "comparison.yaml"),
            "allowed_output_dir": str(self.outputs),
            "timeout_seconds": 30,
        }
        arguments.update(overrides)
        return json.loads(server.create_yaml_and_run_kepler_formal(**arguments))

    def run_yaml(self, path, **overrides):
        arguments = {
            "yaml_file": str(path),
            "allowed_output_dir": str(self.outputs),
            "timeout_seconds": 30,
        }
        arguments.update(overrides)
        return json.loads(server.run_kepler_formal_yaml(**arguments))

    def assert_verdict(self, result, verdict):
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["verdict"], verdict, result)
        self.assertEqual(result["verification_result"]["status"], verdict, result)
        self.assertTrue(Path(result["generated_log_file"]).is_file(), result)

    def assert_error(self, result, message=None):
        self.assertEqual(result["status"], "error", result)
        if message:
            self.assertIn(message.lower(), result["stderr_tail"].lower(), result)

    def test_equivalent_designs_with_identical_top_names(self):
        result = self.create()
        self.assert_verdict(result, "equivalent")
        self.assertEqual(result["exit_code"], 0)
        generated = Path(result["generated_yaml"])
        self.assertEqual(generated, self.outputs / "comparison.yaml")
        configuration = yaml.safe_load(generated.read_text(encoding="utf-8"))
        self.assertEqual(configuration["input_paths"], [str(self.reference), str(self.candidate)])
        self.assertFalse(configuration.get("cnf_export", False))

    def test_difference_is_a_completed_operation_and_has_structured_verdict(self):
        self.candidate.write_text(CONSTANT_OUTPUT, encoding="utf-8")
        result = self.create()
        self.assert_verdict(result, "different")
        # Native completion codes alone do not distinguish these two verdicts.
        self.assertEqual(result["exit_code"], 0)

    def test_sec_options_and_nonzero_difference_code_keep_semantic_verdict(self):
        self.candidate.write_text(CONSTANT_OUTPUT, encoding="utf-8")
        result = self.create(verification="sec", sec_engine="pdr", sec_encoding="binary", max_k=2)
        self.assert_verdict(result, "different")
        self.assertEqual(result["verification_result"]["verification"], "sec")
        self.assertNotEqual(result["exit_code"], 0)

    def test_documented_examples_run_with_the_published_parser(self):
        import re

        guide = Path(__file__).resolve().parents[1] / "docs/test-generation.md"
        sources = re.findall(r"```verilog\n(.*?)```", guide.read_text(encoding="utf-8"), re.S)
        self.assertEqual(len(sources), 3)
        self.reference.write_text(sources[0], encoding="utf-8")
        for source, verdict in zip(sources[1:], ("equivalent", "different")):
            self.candidate.write_text(source, encoding="utf-8")
            self.assert_verdict(self.create(), verdict)

    def test_liberty_is_loaded_for_both_designs(self):
        library = self.designs / "cells.lib"
        library.write_text(LIBERTY, encoding="utf-8")
        cell_design = "module top(input a, output y); %s gate(.A(a), .Y(y)); endmodule\n"
        self.reference.write_text(cell_design % "BUF", encoding="utf-8")
        for cell, verdict in [("BUF", "equivalent"), ("INV", "different")]:
            with self.subTest(cell=cell):
                self.candidate.write_text(cell_design % cell, encoding="utf-8")
                self.assert_verdict(self.create(liberty_files=[str(library)]), verdict)

    def test_existing_yaml_uses_its_own_directory_and_is_not_rewritten(self):
        configuration = self.designs / "existing.yaml"
        original = (
            "# Preserve the user's comments and relative paths.\n"
            "format: verilog\n"
            "input_paths: [reference.v, candidate.v]\n"
            "liberty_files: []\n"
            "solver: kissat\n"
            "log_file: ../outputs/from-yaml.log\n"
        )
        configuration.write_text(original, encoding="utf-8")
        with working_directory(self.outputs):
            result = self.run_yaml(configuration)
        self.assert_verdict(result, "equivalent")
        self.assertEqual(Path(result["generated_log_file"]), self.outputs / "from-yaml.log")
        self.assertEqual(configuration.read_text(encoding="utf-8"), original)

    def test_log_override_does_not_modify_input_yaml(self):
        configuration = self.designs / "existing.yaml"
        original = yaml.safe_dump({
            "input_paths": ["reference.v", "candidate.v"],
            "liberty_files": [],
            "log_file": "old.log",
        })
        configuration.write_text(original, encoding="utf-8")
        expected_log = self.outputs / "override.log"
        result = self.run_yaml(configuration, log_file_name=str(expected_log))
        self.assert_verdict(result, "equivalent")
        self.assertEqual(Path(result["generated_log_file"]), expected_log)
        self.assertEqual(configuration.read_text(encoding="utf-8"), original)
        self.assertFalse((self.designs / "old.log").exists())

    def test_environment_selects_output_directory_and_inputs_resolve_from_cwd(self):
        with working_directory(self.designs), patch.dict(
            os.environ, {"KEPLER_FORMAL_AI_OUTPUT_DIR": str(self.outputs)}
        ):
            result = self.create(
                input_paths=["reference.v", "candidate.v"],
                allowed_output_dir=None,
            )
        self.assert_verdict(result, "equivalent")

    def test_requires_exactly_two_designs(self):
        for paths in [[], [str(self.reference)], [str(self.reference)] * 3]:
            with self.subTest(paths=paths):
                self.assert_error(self.create(input_paths=paths), "two")

    def test_missing_input_and_invalid_verilog_are_reported(self):
        self.assert_error(self.create(input_paths=[str(self.reference), str(self.designs / "missing.v")]))
        self.candidate.write_text("this is not valid Verilog\n", encoding="utf-8")
        self.assert_error(self.create())

    def test_cnf_export_is_rejected_instead_of_silently_ignored(self):
        self.assert_error(self.create(cnf_export=True), "cnf_export")

    def test_yaml_rejects_unknown_keys_and_unsupported_options(self):
        configuration = self.designs / "invalid.yaml"
        base = {
            "input_paths": ["reference.v", "candidate.v"],
            "liberty_files": [],
            "log_file": str(self.outputs / "validation.log"),
        }
        for extra in [
            {"misspelled_option": True},
            {"format": "blif"},
            {"cnf_export": True},
            {"solver": "not-a-solver"},
            {"verification": "not-a-mode"},
        ]:
            with self.subTest(extra=extra):
                configuration.write_text(yaml.safe_dump({**base, **extra}), encoding="utf-8")
                self.assert_error(self.run_yaml(configuration))

    def test_invalid_yaml_is_a_structured_error(self):
        configuration = self.designs / "invalid.yaml"
        for text in ["input_paths: [\n", "- not\n- a\n- mapping\n"]:
            with self.subTest(text=text):
                configuration.write_text(text, encoding="utf-8")
                self.assert_error(self.run_yaml(configuration))

    def test_generated_files_cannot_escape_allowed_directory(self):
        outside = self.root / "outside.yaml"
        self.assert_error(self.create(yaml_output_path=str(outside)))
        self.assertFalse(outside.exists())
        outside_log = self.root / "outside.log"
        self.assert_error(self.create(log_file_name=str(outside_log)))
        self.assertFalse(outside_log.exists())

    def test_generated_files_cannot_overwrite_designs(self):
        for options in ({"yaml_output_path": str(self.reference)},
                        {"log_file_name": str(self.reference)}):
            with self.subTest(options=options):
                self.assert_error(self.create(allowed_output_dir=str(self.root), **options), "overwrite")
                self.assertEqual(self.reference.read_text(encoding="utf-8"), PASS_THROUGH)

    def test_yaml_log_cannot_escape_allowed_directory(self):
        configuration = self.designs / "escape.yaml"
        configuration.write_text(yaml.safe_dump({
            "input_paths": ["reference.v", "candidate.v"],
            "log_file": "outside.log",
        }), encoding="utf-8")
        self.assert_error(self.run_yaml(configuration))
        self.assertFalse((self.designs / "outside.log").exists())

    def test_worker_timeout_is_reported_without_crashing_the_server(self):
        with patch("kepler_formal_mcp.runner.subprocess.run", side_effect=subprocess.TimeoutExpired(
            [sys.executable, "-m", "kepler_formal_mcp.worker"], timeout=1
        )) as run:
            self.assert_error(self.create(timeout_seconds=1), "timed out")
        self.assertEqual(run.call_args.kwargs["timeout"], 1)

    def test_native_worker_crash_is_reported_without_a_result_file(self):
        with patch("kepler_formal_mcp.runner.subprocess.run", return_value=subprocess.CompletedProcess(
            args=[sys.executable], returncode=-11, stdout="", stderr="native worker crash"
        )):
            result = self.create()
        self.assert_error(result, "native worker crash")
        self.assertEqual(result["exit_code"], -11)

    def test_native_output_does_not_corrupt_stdio_mcp(self):
        async def exercise():
            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "kepler_formal_mcp"],
                cwd=str(self.root),
                env=dict(os.environ),
            )
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
                async with stdio_client(parameters, errlog=errors) as (reader, writer):
                    async with ClientSession(reader, writer, read_timeout_seconds=timedelta(seconds=45)) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        names = {tool.name for tool in tools.tools}
                        self.assertTrue({"run_kepler_formal_yaml", "create_yaml_and_run_kepler_formal"} <= names)
                        response = await session.call_tool("get_kepler_formal_info", {})
                        self.assertFalse(response.isError, response)
                        information = json.loads(next(item.text for item in response.content if item.type == "text"))
                        self.assertEqual(information["status"], "success", information)
                        self.assertIn("sec", information["enums"]["VerificationMode"])
                        for content, verdict in [(PASS_THROUGH, "equivalent"), (CONSTANT_OUTPUT, "different")]:
                            self.candidate.write_text(content, encoding="utf-8")
                            response = await session.call_tool("create_yaml_and_run_kepler_formal", {
                                "input_paths": [str(self.reference), str(self.candidate)],
                                "liberty_files": [],
                                "solver": "glucose",
                                "yaml_output_path": str(self.outputs / "stdio.yaml"),
                                "allowed_output_dir": str(self.outputs),
                                "timeout_seconds": 30,
                            })
                            self.assertFalse(response.isError, response)
                            text = next(item.text for item in response.content if item.type == "text")
                            self.assert_verdict(json.loads(text), verdict)
                        # A second protocol operation also works after native solver logging.
                        self.assertEqual(names, {tool.name for tool in (await session.list_tools()).tools})

                        async def call(name, arguments):
                            response = await session.call_tool(name, arguments)
                            self.assertFalse(response.isError, response)
                            result = json.loads(next(item.text for item in response.content if item.type == "text"))
                            self.assertEqual(result["status"], "success", result)
                            return result

                        opened = await call("open_session", {"allowed_output_dir": str(self.outputs)})
                        try:
                            await call("load_designs", {
                                "input_paths": [str(self.reference), str(self.candidate)],
                                "timeout_seconds": 30,
                            })
                            self.reference.unlink()
                            self.candidate.unlink()
                            for mode in ("lec", "sec"):
                                result = await call("verify_session", {
                                    "verification": mode, "solver": "glucose", "timeout_seconds": 30,
                                })
                                self.assert_verdict(result, "different")
                                self.assertEqual(result["pid"], opened["pid"])
                            information = await call("get_kepler_formal_info", {})
                            self.assertEqual(information["pid"], opened["pid"])
                            self.assertEqual(information["session_id"], opened["session_id"])
                        finally:
                            await call("close_session", {"session_id": opened["session_id"]})

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
