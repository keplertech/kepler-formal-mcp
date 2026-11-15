"""Keep the MCP verification surface aligned with the installed public KF API."""

from __future__ import annotations

import asyncio
from dataclasses import fields
import inspect
from itertools import product
import json
from pathlib import Path
import tempfile
from typing import get_args
import unittest

import kepler_formal as kf

from kepler_formal_mcp import server


class PublishedApiCoverageTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="kepler api coverage ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.reference = self.root / "reference.v"
        self.candidate = self.root / "candidate.v"
        self.reference.write_text(
            "module top(input a, output y); assign y = a; endmodule\n",
            encoding="utf-8",
        )

    def compare(self, expected: str, **options) -> dict:
        expression = "a" if expected == "equivalent" else "1'b0"
        self.candidate.write_text(
            f"module top(input a, output y); assign y = {expression}; endmodule\n",
            encoding="utf-8",
        )
        response = json.loads(server.create_yaml_and_run_kepler_formal(
            input_paths=[str(self.reference), str(self.candidate)],
            liberty_files=[],
            yaml_output_path=str(self.root / "comparison.yaml"),
            allowed_output_dir=str(self.root),
            timeout_seconds=30,
            **options,
        ))
        self.assertEqual(response["status"], "success", response)
        self.assertEqual(response["verdict"], expected, response)
        result = response["verification_result"]
        self.assertEqual(result["status"], expected, response)
        self.assertEqual(result["equivalent"], expected == "equivalent", response)
        self.assertTrue(result["conclusive"], response)
        # Every public result field is preserved, including coverage details and
        # diagnostics which callers cannot reconstruct from an exit code.
        self.assertEqual(
            set(result),
            {field.name for field in fields(kf.VerificationResult)}
            | {"equivalent", "conclusive", "coverage_percent"},
        )
        self.assertIn(result["status"], {status.value for status in kf.VerificationStatus})
        return response

    def test_every_published_solver_handles_both_lec_verdicts(self):
        for solver, expected in product(kf.Solver, ("equivalent", "different")):
            with self.subTest(solver=solver.value, expected=expected):
                result = self.compare(expected, solver=solver.value)
                self.assertEqual(result["verification_result"]["verification"], "lec")

    def test_every_sec_engine_and_encoding_handles_both_verdicts(self):
        for engine, encoding, expected in product(
            kf.SecEngine, kf.SecEncoding, ("equivalent", "different")
        ):
            with self.subTest(engine=engine.value, encoding=encoding.value, expected=expected):
                result = self.compare(
                    expected,
                    verification="sec",
                    max_k=2,
                    sec_engine=engine.value,
                    sec_encoding=encoding.value,
                )
                verification = result["verification_result"]
                self.assertEqual(verification["verification"], "sec")
                self.assertEqual(verification["total_outputs"], 1)
                self.assertEqual(verification["covered_outputs"], 1)
                self.assertEqual(verification["coverage_percent"], 100.0)

    def test_none_log_level_is_supported_like_the_python_api(self):
        self.compare("equivalent", log_level=None)

    def test_boundary_mismatch_requires_explicit_permission(self):
        # The LEC boundary check concerns input names. Renaming this input
        # exercises the flag; simply adding an output does not.
        self.candidate.write_text(
            "module top(input b, output y); assign y = b; endmodule\n",
            encoding="utf-8",
        )
        arguments = {
            "input_paths": [str(self.reference), str(self.candidate)],
            "liberty_files": [],
            "yaml_output_path": str(self.root / "boundary.yaml"),
            "allowed_output_dir": str(self.root),
            "timeout_seconds": 30,
        }
        rejected = json.loads(server.create_yaml_and_run_kepler_formal(**arguments))
        self.assertEqual(rejected["status"], "error", rejected)
        self.assertIn("boundary mismatch", rejected["verification_result"]["reason"].lower())
        permitted = json.loads(server.create_yaml_and_run_kepler_formal(
            **arguments, allow_boundary_mismatch=True,
        ))
        self.assertEqual(permitted["status"], "success", permitted)
        self.assertEqual(permitted["verdict"], "equivalent", permitted)

    def test_all_public_verification_options_have_tool_arguments(self):
        from kepler_formal_mcp import config, verification

        renamed = {"mode": "verification", "log_file": "log_file_name"}
        native = [field.name for field in fields(kf.VerificationOptions)]
        self.assertEqual(set(verification.OPTION_NAMES), set(native))
        self.assertLessEqual(set(native), config.SUPPORTED_KEYS)
        for tool in (server.create_yaml_and_run_kepler_formal, server.verify_session):
            signature = inspect.signature(tool)
            for name in native:
                with self.subTest(tool=tool.__name__, option=name):
                    self.assertIn(renamed.get(name, name), signature.parameters)

    def test_boolean_tool_defaults_match_the_python_api(self):
        # A tool default is sent explicitly, so a stale copy would override the library.
        for tool in (server.create_yaml_and_run_kepler_formal, server.verify_session):
            signature = inspect.signature(tool)
            for field in fields(kf.VerificationOptions):
                if isinstance(field.default, bool):
                    with self.subTest(tool=tool.__name__, option=field.name):
                        self.assertIs(signature.parameters[field.name].default, field.default)

    def test_sec_model_switches_can_be_disabled_only_for_sec(self):
        from kepler_formal_mcp import config

        for option in config.SEC_MODEL_OPTIONS:
            with self.subTest(option=option):
                self.compare("equivalent", verification="sec", max_k=2, **{option: False})
                rejected = json.loads(server.create_yaml_and_run_kepler_formal(
                    input_paths=[str(self.reference), str(self.candidate)], liberty_files=[],
                    yaml_output_path=str(self.root / "lec.yaml"),
                    allowed_output_dir=str(self.root), timeout_seconds=30, **{option: False},
                ))
                self.assertEqual(rejected["status"], "error", rejected)
                self.assertIn(option, rejected["stderr_tail"])
                self.assertFalse((self.root / "lec.yaml").exists())

    def test_advertised_enums_match_the_installed_python_api(self):
        from kepler_formal_mcp import options

        advertised = {
            "verification": (options.Mode, kf.VerificationMode),
            "solver": (options.Solver, kf.Solver),
            "sec_engine": (options.SecEngine, kf.SecEngine),
            "sec_encoding": (options.SecEncoding, kf.SecEncoding),
        }
        tools = asyncio.run(server.app.list_tools())
        tool = next(tool for tool in tools if tool.name == "create_yaml_and_run_kepler_formal")
        properties = tool.inputSchema["properties"]

        def enum_values(schema):
            values = set(schema.get("enum", ()))
            for alternative in schema.get("anyOf", ()):
                values.update(enum_values(alternative))
            return values

        for argument, (alias, native_enum) in advertised.items():
            expected = {member.value for member in native_enum}
            with self.subTest(argument=argument):
                self.assertEqual(set(get_args(alias)), expected)
                self.assertEqual(enum_values(properties[argument]), expected)
        self.assertEqual(enum_values(properties["log_level"]), set(get_args(options.LogLevel)))


if __name__ == "__main__":
    unittest.main()
