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
  cell(AND2) {
    pin(A) { direction : input; }
    pin(B) { direction : input; }
    pin(Y) { direction : output; function : "A&B"; }
  }
}
"""
CELL_DESIGN = "module top(input a, output y); %s gate(.A(a), .Y(y)); endmodule\n"
# These two agree only while rst is low.
RESET_FREE = "module top(input a, input rst, output y); BUF gate(.A(a), .Y(y)); endmodule\n"
RESET_GATED = """module top(input a, input rst, output y);
  wire enabled;
  INV invert(.A(rst), .Y(enabled));
  AND2 gate(.A(a), .B(enabled), .Y(y));
endmodule
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

    def cell_library(self):
        return [str(self.write_design("cells.lib", LIBERTY))]

    def generated_configuration(self, result):
        return yaml.safe_load(Path(result["generated_yaml"]).read_text(encoding="utf-8"))

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

    def test_boundary_pairs_abstract_paired_leaf_instances(self):
        libraries = self.cell_library()
        self.reference.write_text(CELL_DESIGN % "BUF", encoding="utf-8")
        self.candidate.write_text(CELL_DESIGN % "INV", encoding="utf-8")
        for mode in ("lec", "sec"):
            with self.subTest(mode=mode):
                self.assert_verdict(self.create(liberty_files=libraries, verification=mode), "different")
                result = self.create(liberty_files=libraries, verification=mode,
                                     set_as_boundary=[("gate", "gate")])
                self.assert_verdict(result, "equivalent")
                self.assertEqual(self.generated_configuration(result)["set_as_boundary"], [["gate", "gate"]])
        missing = self.create(liberty_files=libraries, set_as_boundary=[["absent", "gate"]])
        self.assertEqual(missing["status"], "error", missing)
        self.assertIn("absent", missing["verification_result"]["reason"])

    def test_reset_bootstrap_holds_the_listed_ports(self):
        libraries = self.cell_library()
        self.reference.write_text(RESET_FREE, encoding="utf-8")
        self.candidate.write_text(RESET_GATED, encoding="utf-8")

        def sec(**options):
            return self.create(liberty_files=libraries, verification="sec", **options)

        self.assert_verdict(sec(), "different")
        released = sec(sec_reset_cycles=1, sec_reset_ports=[["rst", True]])
        self.assert_verdict(released, "equivalent")
        configuration = self.generated_configuration(released)
        self.assertEqual(configuration["sec_reset_cycles"], 1)
        self.assertEqual(configuration["sec_reset_ports"], [["rst", True]])
        # Asserted low, rst is then released high, where the designs differ.
        self.assert_verdict(sec(sec_reset_cycles=1, sec_reset_ports=[["rst", False]]), "different")
        for options in (
            {"sec_reset_cycles": 1},
            {"sec_reset_ports": [["rst", True]]},
            {"sec_reset_cycles": 0, "sec_reset_ports": [["rst", True]]},
            {"sec_reset_cycles": True, "sec_reset_ports": [["rst", True]]},
            {"sec_reset_cycles": 1, "sec_reset_ports": [["rst", 1]]},
            {"sec_reset_cycles": 1, "sec_reset_ports": [["rst"]]},
            {"sec_reset_cycles": 1, "sec_reset_ports": "rst"},
            {"sec_reset_cycles": 1, "sec_reset_ports": [["rst", True], ["rst", False]]},
        ):
            with self.subTest(options=options):
                self.assert_error(sec(**options), "sec_reset")
        self.assert_error(self.create(liberty_files=libraries, sec_reset_cycles=1,
                                      sec_reset_ports=[["rst", True]]), "verification: sec")

    def test_btor2_export_is_written_inside_the_output_directory(self):
        export = self.outputs / "exports" / "obligation.btor2"
        dumped = self.create(verification="sec", btor2_export_path="exports/obligation.btor2",
                             btor2_dump_only=True)
        self.assert_verdict(dumped, "exported")
        self.assertEqual(dumped["exit_code"], 0)
        self.assertEqual(Path(dumped["generated_btor2_file"]), export)
        self.assertIn("sort bitvec", export.read_text(encoding="utf-8"))
        self.assertEqual(self.generated_configuration(dumped)["btor2_export_path"], str(export))
        # Without btor2_dump_only the proof still runs after the export.
        export.unlink()
        proved = self.create(verification="sec", btor2_export_path=str(export))
        self.assert_verdict(proved, "equivalent")
        self.assertEqual(Path(proved["generated_btor2_file"]), export)
        self.assertTrue(export.is_file())

    def test_btor2_export_rejects_unsafe_or_inapplicable_requests(self):
        outside = self.root / "outside.btor2"
        for options, message in (
            ({"verification": "sec", "btor2_export_path": str(outside)}, "not allowed outside"),
            ({"verification": "sec", "btor2_dump_only": True}, "requires btor2_export_path"),
            ({"verification": "sec", "btor2_export_path": ""}, "nonempty"),
            ({"btor2_export_path": "lec.btor2"}, "verification: sec"),
            ({"verification": "sec", "btor2_export_path": "comparison.yaml"}, "overwrite"),
            ({"verification": "sec", "btor2_export_path": "shared.log",
              "log_file_name": "shared.log"}, "overwrite"),
        ):
            with self.subTest(options=options):
                self.assert_error(self.create(**options), message)
        self.assertFalse(outside.exists())
        self.assertEqual(list(self.outputs.iterdir()), [])

    def test_stale_btor2_file_is_not_reported_as_this_runs_export(self):
        stale = self.outputs / "stale.btor2"
        stale.write_text("from an earlier run\n", encoding="utf-8")
        with patch("kepler_formal_mcp.runner.subprocess.run", return_value=subprocess.CompletedProcess(
            args=[sys.executable], returncode=-11, stdout="", stderr="native worker crash"
        )):
            result = self.create(verification="sec", btor2_export_path=str(stale))
        self.assert_error(result, "native worker crash")
        self.assertIsNone(result["generated_btor2_file"])

    def test_existing_yaml_accepts_the_python_option_names(self):
        self.cell_library()
        self.reference.write_text(RESET_FREE, encoding="utf-8")
        self.candidate.write_text(RESET_GATED, encoding="utf-8")
        configuration = self.designs / "options.yaml"
        configuration.write_text(yaml.safe_dump({
            "input_paths": ["reference.v", "candidate.v"],
            "liberty_files": ["cells.lib"],
            "verification": "sec",
            "sec_reset_cycles": 2,
            "sec_reset_ports": [["rst", True]],
            "set_as_boundary": [],
            "learn_internal_relations": False,
            "allow_x_equality_in_internal_relations": False,
            "binary_relation_round": False,
            "latch_abstraction": False,
            "data_latch_model": False,
            "btor2_export_path": "../outputs/from-yaml.btor2",
            "btor2_dump_only": False,
            "log_file": "../outputs/from-yaml.log",
        }), encoding="utf-8")
        result = self.run_yaml(configuration)
        self.assert_verdict(result, "equivalent")
        self.assertEqual(Path(result["generated_btor2_file"]), self.outputs / "from-yaml.btor2")
        self.assertTrue((self.outputs / "from-yaml.btor2").is_file())

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
            # The YAML keys are the Python option names, not the CLI spellings.
            {"verification": "sec", "sec_reset": {"cycles": 1, "ports": [{"name": "a", "active_value": 1}]}},
            {"verification": "sec", "btor2_export": True},
            {"verification": "sec", "dump_only": True},
            {"set_as_boundary": "gate"},
            {"set_as_boundary": [["only-one-path"]]},
            {"set_as_boundary": [["gate", ""]]},
            {"verification": "sec", "latch_abstraction": "no"},
            {"verification": "sec", "data_latch_model": None},
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
                        {"log_file_name": str(self.reference)},
                        {"verification": "sec", "btor2_export_path": str(self.reference)}):
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

                        # Lists of pairs arrive intact through the tool schema and transport.
                        library = self.cell_library()
                        for first, second, options in (
                            (CELL_DESIGN % "BUF", CELL_DESIGN % "INV", {"set_as_boundary": [["gate", "gate"]]}),
                            (RESET_FREE, RESET_GATED, {"sec_reset_cycles": 1, "sec_reset_ports": [["rst", True]]}),
                        ):
                            response = await session.call_tool("create_yaml_and_run_kepler_formal", {
                                "input_paths": [str(self.write_design("first.v", first)),
                                                str(self.write_design("second.v", second))],
                                "liberty_files": library, "verification": "sec",
                                "yaml_output_path": str(self.outputs / "pairs.yaml"),
                                "allowed_output_dir": str(self.outputs),
                                "timeout_seconds": 30, **options,
                            })
                            self.assertFalse(response.isError, response)
                            text = next(item.text for item in response.content if item.type == "text")
                            self.assert_verdict(json.loads(text), "equivalent")

                        async def call(name, arguments):
                            response = await session.call_tool(name, arguments)
                            self.assertFalse(response.isError, response)
                            result = json.loads(next(item.text for item in response.content if item.type == "text"))
                            self.assertEqual(result["status"], "success", result)
                            return result

                        opened = await call("open_session", {"allowed_output_dir": str(self.outputs)})
                        try:
                            loaded = await call("load_designs", {
                                "input_paths": [str(self.reference), str(self.candidate)],
                                "timeout_seconds": 30,
                            })
                            self.reference.unlink()
                            self.candidate.unlink()
                            for mode in ("lec", "sec"):
                                result = await call("verify_session", {
                                    "design1": loaded["loaded"][0], "design2": loaded["loaded"][1],
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
