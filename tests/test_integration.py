"""Opt-in native SEC tests: KEPLER_FORMAL_INTEGRATION_BIN must name a packaged binary."""

import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import yaml

import server


LIBERTY = '''library(fixture) {
  delay_model : table_lookup;
  time_unit : "1ns";
  voltage_unit : "1V";
  current_unit : "1mA";
  capacitive_load_unit(1,pf);
  cell(BUF) { area : 1; pin(A) { direction : input; }
    pin(Y) { direction : output; function : "A"; } }
  cell(INV) { area : 1; pin(A) { direction : input; }
    pin(Y) { direction : output; function : "!A"; } }
}'''


@unittest.skipUnless(os.environ.get("KEPLER_FORMAL_INTEGRATION_BIN"), "Packaged Kepler integration binary not configured")
class NativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name, text in {
            "cells.lib": LIBERTY,
            "golden.v": "module top(A, Y); input A; output Y; BUF g (.A(A), .Y(Y)); endmodule\n",
            "equivalent.v": "module top(A, Y); input A; output Y; wire n; BUF g0 (.A(A), .Y(n)); BUF g1 (.A(n), .Y(Y)); endmodule\n",
            "different.v": "module top(A, Y); input A; output Y; INV g (.A(A), .Y(Y)); endmodule\n",
            "broken.v": "this is not valid Verilog\n",
        }.items():
            (self.root / name).write_text(text)
        self.env = patch.dict(os.environ,
            KEPLER_FORMAL_WORKSPACE=str(self.root),
            KEPLER_FORMAL_AI_OUTPUT_DIR=str(self.root / "runs"),
            KEPLER_FORMAL_BIN=os.environ["KEPLER_FORMAL_INTEGRATION_BIN"])
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_packaged_engine_proves_equivalence_through_mcp(self):
        async def exercise():
            params = StdioServerParameters(command=sys.executable,
                args=[str(Path(server.__file__).resolve())], env=dict(os.environ))
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as session:
                    await session.initialize()
                    reply = await session.call_tool("verify_sec", {
                        "golden": "golden.v", "revised": "equivalent.v", "liberty_files": ["cells.lib"]})
                    self.assertFalse(reply.isError)
                    result = reply.structuredContent
                    self.assertEqual(result["status"], "proved", result)
                    self.assertEqual(result["coverage"]["proved_outputs"], 1)
                    self.assertEqual(result["coverage"]["skipped_outputs"], 0)
                    config = yaml.safe_load(Path(result["yaml_config"]).read_text())
                    self.assertEqual(config["verification"], "sec")
                    self.assertTrue(config["report_skipped_pos"])
                    self.assertTrue(result["equivalent"])
                    self.assertEqual(json.loads(Path(result["result_file"]).read_text())["status"], "proved")
        asyncio.run(exercise())

    def test_packaged_engine_rejects_behavioral_difference_via_yaml(self):
        path = self.root / "request.yaml"
        text = "input_paths: [golden.v, different.v]\nliberty_files: [cells.lib]\n"
        path.write_text(text)
        result = server.run_kepler_formal_yaml(str(path), timeout_seconds=60)
        self.assertEqual(result.status, "counterexample", result.model_dump())
        self.assertFalse(result.equivalent)
        self.assertTrue(result.blocking)
        self.assertEqual(result.exit_code, 3)
        self.assertEqual(path.read_text(), text)

    def test_packaged_engine_parse_failure_is_blocking(self):
        result = server.create_yaml_and_run_kepler_formal(["golden.v", "broken.v"], ["cells.lib"], timeout_seconds=60)
        self.assertEqual(result.status, "error", result.model_dump())
        self.assertTrue(result.blocking)
        self.assertIsNone(result.equivalent)
        self.assertTrue(Path(result.stderr_log).exists())


if __name__ == "__main__":
    unittest.main()
