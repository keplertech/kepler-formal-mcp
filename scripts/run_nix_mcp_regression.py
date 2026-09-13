#!/usr/bin/env python3
"""Exercise both MCP tools against an explicitly installed Kepler Formal release."""

from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 60
TOOLS = {"run_kepler_formal_yaml", "create_yaml_and_run_kepler_formal"}


def _artifact(result: dict, key: str, case_dir: Path) -> Path:
    value = result.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing artifact path: {key}")
    path = Path(value).resolve()
    if not path.is_relative_to(case_dir.resolve()) or not path.is_file():
        raise ValueError(f"Missing or external {key}: {path}")
    if path.stat().st_size == 0:
        raise ValueError(f"Empty {key}: {path}")
    return path


def validate_result(result: dict, *, verdict: str, exit_code: int, case_dir: Path) -> dict:
    """Require real verdict/coverage evidence, not just a successful process."""
    if verdict not in {"sec_equivalent", "sec_counterexample", "lec_equivalent"}:
        raise ValueError(f"Unknown expected verdict: {verdict}")
    if type(result.get("exit_code")) is not int or result["exit_code"] != exit_code:
        raise ValueError(f"Expected exit {exit_code}, got {result.get('exit_code')!r}")
    expected_status = "success" if exit_code == 0 else "error"
    if result.get("status") != expected_status:
        raise ValueError(f"Expected wrapper status {expected_status}, got {result.get('status')!r}")
    _artifact(result, "yaml_config", case_dir)
    log = _artifact(result, "generated_log_file", case_dir)
    output = "\n".join((
        result.get("stdout_tail", ""), result.get("stderr_tail", ""),
        log.read_text(encoding="utf-8", errors="replace"),
    ))
    if re.search(r"SEC partially proved|SEC was inconclusive|SEC cannot run|proof not run", output, re.I):
        raise ValueError("Checker did not complete the required full check")
    proof = "SEC proved equivalence" in output
    counterexample = "SEC found a counterexample" in output
    coverage = None
    if verdict.startswith("sec_"):
        if verdict == "sec_equivalent" and (not proof or counterexample):
            raise ValueError("Expected an explicit SEC proof without a counterexample")
        if verdict == "sec_counterexample" and (not counterexample or proof):
            raise ValueError("Expected an explicit SEC counterexample without a proof")
        rows = re.findall(
            r"SEC checked-output coverage:\s*([0-9.]+)%\s*\((\d+)/(\d+) covered/existing outputs\)",
            output,
        )
        if (not rows or len(rows) != output.count("SEC checked-output coverage:")
                or any((float(p), int(c), int(t)) != (100.0, 1, 1) for p, c, t in rows)):
            raise ValueError(f"Expected 100% checked-output coverage (1/1), got {rows!r}")
        coverage = {"percent": 100.0, "covered": 1, "existing": 1}
    elif verdict == "lec_equivalent":
        if "No difference was found." not in output or re.search(r"(?<!No )Difference was found", output, re.I):
            raise ValueError("Expected an explicit LEC equivalence verdict")
    else:
        raise ValueError(f"Unknown expected verdict: {verdict}")
    return {"verdict": verdict, "exit_code": exit_code, "coverage": coverage}


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


async def _call_tool(server: Path, binary: Path, case_dir: Path, tool: str, arguments: dict) -> dict:
    # The current server roots native side reports beside __file__. Stage an
    # identical single-file server per case to preserve every run's artifacts.
    staged_server = case_dir / "server.py"
    shutil.copyfile(server, staged_server)
    environment = os.environ.copy()
    for key in ("PYTHONHOME", "PYTHONPATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
        environment.pop(key, None)
    environment.update(KEPLER_FORMAL_BINARY=str(binary), KEPLER_FORMAL_AI_OUTPUT_DIR=str(case_dir))
    params = StdioServerParameters(
        command=sys.executable, args=[str(staged_server)], env=environment, cwd=str(case_dir),
    )
    with (case_dir / "mcp-stderr.log").open("w", encoding="utf-8") as errors:
        async with stdio_client(params, errlog=errors) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=90)) as session:
                await session.initialize()
                available = [item.name for item in (await session.list_tools()).tools]
                _write_json(case_dir / "tools.json", available)
                if not TOOLS.issubset(available):
                    raise ValueError(f"Missing MCP tools: {TOOLS - set(available)}")
                response = await session.call_tool(tool, arguments)
                _write_json(case_dir / "mcp-response.json", response.model_dump(mode="json"))
                if response.isError:
                    raise ValueError("MCP returned a protocol/tool error; see mcp-response.json")
                text = [item.text for item in response.content if item.type == "text"]
                if len(text) != 1:
                    raise ValueError("Expected one JSON tool result")
                result = json.loads(text[0])
                if not isinstance(result, dict):
                    raise ValueError("Expected a JSON result object")
                _write_json(case_dir / "result.json", result)
                return result


def _fixture(case_dir: Path, name: str, contents: str) -> str:
    path = case_dir / name
    path.write_text(contents, encoding="utf-8")
    return str(path)


def _prepare_case(case_dir: Path, name: str) -> tuple[str, dict, str, int]:
    case_dir.mkdir()
    base_args = {"allowed_output_dir": str(case_dir), "timeout_seconds": TIMEOUT_SECONDS}
    if name in {"sec_equivalent", "sec_counterexample"}:
        expression = "(d ^ 1'b0)" if name == "sec_equivalent" else "~d"
        def register(value: str) -> str:
            return (
                "module top(input logic clk, reset, d, output logic q);\n"
                f"  always_ff @(posedge clk) q <= reset ? 1'b0 : {value};\nendmodule\n"
            )
        reference = _fixture(case_dir, "reference.sv", register("d"))
        candidate = _fixture(case_dir, "candidate.sv", register(expression))
        config = (
            "format: systemverilog\nverification: sec\nsec_engine: pdr\n"
            "sec_encoding: dual_rail_steady\nmax_k: 4\nreport_skipped_pos: true\n"
            "sv_design1_top: top\nsv_design2_top: top\n"
            f"input_paths: {json.dumps([reference, candidate])}\n"
            f"log_file: {json.dumps(str(case_dir / 'checker.log'))}\n"
        )
        args = {**base_args, "yaml_file": _fixture(case_dir, "config.yaml", config)}
        return "run_kepler_formal_yaml", args, name, 0 if name == "sec_equivalent" else 3
    direct = _fixture(case_dir, "direct.v", "module top(input a, output y); assign y = a; endmodule\n")
    if name == "generated_lec":
        candidate = _fixture(case_dir, "candidate.v", "module top(input a, output y); assign y = a; endmodule\n")
        args = {
            **base_args, "input_paths": [direct, candidate], "liberty_files": [],
            "cnf_export": False, "yaml_output_path": "generated.yaml",
        }
        return "create_yaml_and_run_kepler_formal", args, "lec_equivalent", 0
    mapped = _fixture(case_dir, "mapped.v", "module top(input a, output y); BUF u_buf(.A(a), .Z(y)); endmodule\n")
    primitives = _fixture(case_dir, "primitives.py", (
        "import naja\n\ndef constructPrimitives(lib):\n"
        "    cell = naja.SNLDesign.createPrimitive(lib, 'BUF')\n"
        "    naja.SNLScalarTerm.create(cell, naja.SNLTerm.Direction.Input, 'A')\n"
        "    naja.SNLScalarTerm.create(cell, naja.SNLTerm.Direction.Output, 'Z')\n"
        "    cell.setTruthTable(0b10)\n"
    ))
    config = (
        "format: verilog\nverification: lec\n"
        f"input_paths: {json.dumps([mapped, direct])}\n"
        f"py_tech_files: {json.dumps([primitives])}\n"
        f"log_file: {json.dumps(str(case_dir / 'checker.log'))}\n"
    )
    args = {**base_args, "yaml_file": _fixture(case_dir, "config.yaml", config)}
    return "run_kepler_formal_yaml", args, "lec_equivalent", 0


async def run_regression(binary: Path, server: Path, work_dir: Path) -> dict:
    # Never reuse a prior run's log or config as evidence.
    work_dir.mkdir(parents=True, exist_ok=False)
    summary = {
        "binary": str(binary), "server": str(server),
        "server_sha256": hashlib.sha256(server.read_bytes()).hexdigest(),
        "cases": {}, "passed": False,
    }
    _write_json(work_dir / "summary.json", summary)
    with (work_dir / "checker-help.log").open("w", encoding="utf-8") as output:
        subprocess.run([str(binary), "--help"], cwd=work_dir, stdout=output,
                       stderr=subprocess.STDOUT, timeout=30, check=True)
    for name in ("sec_equivalent", "sec_counterexample", "generated_lec", "python_primitives_lec"):
        case_dir = work_dir / name
        tool, arguments, verdict, exit_code = _prepare_case(case_dir, name)
        inputs = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in case_dir.iterdir() if p.suffix in {".v", ".sv", ".py"}}
        _write_json(case_dir / "input-sha256.json", inputs)
        _write_json(case_dir / "request.json", {"tool": tool, "arguments": arguments})
        try:
            result = await asyncio.wait_for(_call_tool(server, binary, case_dir, tool, arguments), timeout=120)
            evidence = validate_result(result, verdict=verdict, exit_code=exit_code, case_dir=case_dir)
            if name == "generated_lec":
                _artifact(result, "generated_yaml", case_dir)
            if name == "python_primitives_lec" and "Loading python primitive file:" not in result["stdout_tail"]:
                raise ValueError("Missing evidence that the installed Python primitive loader ran")
            for filename, digest in inputs.items():
                if hashlib.sha256((case_dir / filename).read_bytes()).hexdigest() != digest:
                    raise ValueError(f"Input changed during checking: {filename}")
            summary["cases"][name] = {"passed": True, **evidence}
            print(f"PASS {name}: {evidence}", flush=True)
        except Exception as exc:
            summary["cases"][name] = {"passed": False, "error": str(exc)}
            print(f"FAIL {name}: {exc}", file=sys.stderr, flush=True)
        _write_json(work_dir / "summary.json", summary)
    summary["passed"] = all(case["passed"] for case in summary["cases"].values())
    _write_json(work_dir / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path, help="fresh artifact directory; must not exist")
    parser.add_argument("--server", type=Path, default=ROOT / "server.py")
    args = parser.parse_args()
    binary, server, work_dir = (path.expanduser().resolve() for path in (args.binary, args.server, args.work_dir))
    if not binary.is_file() or not os.access(binary, os.X_OK):
        parser.error(f"not an executable: {binary}")
    if not server.is_file():
        parser.error(f"server does not exist: {server}")
    try:
        result = asyncio.run(run_regression(binary, server, work_dir))
    except Exception as exc:
        print(f"Regression setup failed: {exc}", file=sys.stderr)
        return 1
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
